"""
Tests for #49 — Fetch now accepted work it could not do.

A Telegram source fetches through a platform connection, and needs an active
one. The poll has always known that: it skips a source whose connection is not
active, and a missing connection fails that test too. The on-demand path did
not — it returned 202, the background runner then failed to build a connector,
logged a warning, and the user saw a button that took the work and did nothing
with it.

#28 made the state visible on the row. This makes the controls match it, on both
sides: disabled in the UI, and refused by the API so the UI is not the only
thing enforcing it.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import sources as sources_api
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace
from tests.conftest import FakeSession


def _workspace() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), name="ws", topic_scope="forex", ingestion_paused=False
    )


def _source(*, source_type="telegram", connection_status="active", has_connection=True,
            identifier="chan", workspace_id=None):
    connection = (
        SimpleNamespace(id=uuid.uuid4(), status=connection_status, label="a connection")
        if has_connection
        else None
    )
    return SimpleNamespace(
        id=uuid.uuid4(), identifier=identifier, source_type=source_type,
        workspace_id=workspace_id,
        fetch_cadence="hourly", content_filters={"text": True},
        is_paused=False, paused_at=None, last_fetched_id=None,
        last_fetched_item_at=None, last_fetched_at=None,
        platform_connection_id=connection.id if connection else None,
        connection=connection,
    )


async def _fetch(source, workspace=None, *, tasks=None, db=None):
    """Returns the queue and session the call was given, so a refusal can be
    checked against the very objects it might have written to."""
    tasks = tasks if tasks is not None else BackgroundTasks()
    db = db or FakeSession(rows={Workspace: workspace or _workspace(), SourceConfig: source})
    await sources_api.trigger_fetch(
        workspace_id=uuid.uuid4(), source_id=source.id, background_tasks=tasks,
        current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
    )
    return tasks, db


async def _backfill(source, workspace=None, *, tasks=None, db=None):
    tasks = tasks if tasks is not None else BackgroundTasks()
    db = db or FakeSession(rows={Workspace: workspace or _workspace(), SourceConfig: source})
    await sources_api.trigger_backfill(
        workspace_id=uuid.uuid4(), source_id=source.id,
        payload=sources_api.BackfillRequest(
            date_range_start=datetime(2026, 9, 1, tzinfo=timezone.utc), date_range_end=None
        ),
        background_tasks=tasks,
        current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
    )
    return tasks, db


# ── a telegram source with no usable connection ──────────────────────────────

class TestFetchNowRefuses:
    @pytest.mark.parametrize("state", ["revoked", "pending"])
    async def test_a_connection_that_is_not_active(self, state):
        with pytest.raises(HTTPException) as exc:
            await _fetch(_source(connection_status=state))

        assert exc.value.status_code == 409
        assert "connection" in exc.value.detail.lower()

    async def test_no_connection_at_all(self):
        """
        A source added before its platform connection existed, or one whose
        connection row is gone. The poll skips it; so should this.
        """
        with pytest.raises(HTTPException) as exc:
            await _fetch(_source(has_connection=False))

        assert exc.value.status_code == 409

    @pytest.mark.parametrize("state", ["revoked", "pending"])
    async def test_it_queues_nothing(self, state):
        """
        The bug was work accepted and then dropped, so nothing may be queued —
        asserted against the queue the call was handed, not a fresh one, which
        would hold true whatever the code did.
        """
        tasks = BackgroundTasks()

        with pytest.raises(HTTPException):
            await _fetch(_source(connection_status=state), tasks=tasks)

        assert tasks.tasks == []


class TestBackfillRefuses:
    @pytest.mark.parametrize("state", ["revoked", "pending"])
    async def test_a_connection_that_is_not_active(self, state):
        with pytest.raises(HTTPException) as exc:
            await _backfill(_source(connection_status=state))

        assert exc.value.status_code == 409

    async def test_it_creates_no_job_row(self):
        """
        A job row for work that cannot run is a lie in the job list. Asserted
        against the session the call was handed.
        """
        source = _source(has_connection=False)
        db = FakeSession(rows={Workspace: _workspace(), SourceConfig: source})

        with pytest.raises(HTTPException):
            await _backfill(source, db=db)

        assert db.added == []


# ── and what must keep working ───────────────────────────────────────────────

class TestWhatIsStillAllowed:
    async def test_an_active_connection_still_fetches(self):
        """The guard must not become a blanket refusal."""
        tasks, _ = await _fetch(_source(connection_status="active"))

        assert len(tasks.tasks) == 1

    async def test_a_youtube_source_needs_no_connection(self):
        """
        It authenticates with a server-side API key, so a missing connection is
        normal rather than broken — the poll only requires YOUTUBE_API_KEY.
        """
        tasks, _ = await _fetch(_source(source_type="youtube", has_connection=False))

        assert len(tasks.tasks) == 1

    async def test_a_youtube_backfill_is_allowed_too(self):
        _, db = await _backfill(_source(source_type="youtube", has_connection=False))

        assert len(db.added) == 1        # the job row was created


# ── the poll's answer and the endpoint's are now the same one ────────────────

@pytest.fixture
def poll(monkeypatch):
    """_poll_all_sources against a fake session; returns what it tried to fetch."""
    from app.services import billing_service, ingestion_pipeline
    from app.workers import poller

    workspace = _workspace()
    fetched: list[str] = []

    async def _fetch(db, *, source, workspace):
        fetched.append(source.identifier)
        return 1

    async def _no(db, _ws_id):
        return False

    monkeypatch.setattr(ingestion_pipeline, "run_fetch_pipeline", _fetch)
    monkeypatch.setattr(billing_service, "is_payment_lapsed", _no)
    monkeypatch.setattr(billing_service, "is_budget_exhausted", _no)

    async def run(*sources):
        session = FakeSession(
            rows={SourceConfig: list(sources), Workspace: {workspace.id: workspace}}
        )
        monkeypatch.setattr(poller, "AsyncSessionLocal", lambda: session)
        await poller._poll_all_sources()
        return fetched

    return SimpleNamespace(run=run, workspace_id=workspace.id)


class TestThePollAgrees:
    """
    The poll skipped these sources long before #49; the point of sharing one
    predicate is that it still does, and for the same reason the endpoint gives.
    """

    @pytest.mark.parametrize("state", ["revoked", "pending"])
    async def test_it_skips_a_connection_that_is_not_active(self, poll, state):
        fetched = await poll.run(
            _source(identifier="broken", connection_status=state, workspace_id=poll.workspace_id),
            _source(identifier="fine", workspace_id=poll.workspace_id),
        )

        assert fetched == ["fine"]

    async def test_it_skips_a_source_with_no_connection(self, poll):
        fetched = await poll.run(
            _source(identifier="orphan", has_connection=False, workspace_id=poll.workspace_id),
        )

        assert fetched == []
