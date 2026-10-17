"""
Tests for #13 — one switch pauses ingestion for a whole workspace.

The switch has to be orthogonal to the per-source one. A user who paused two
sources deliberately, then paused the workspace for a holiday, must get those
two back as paused when they flip the workspace on again — so the workspace
switch may never write to a source's own paused state, and must never touch a
fetch cursor either.

It also has to be a pause on spend, not a lockout: nothing here touches reading.

Offline like the rest of the suite.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import sources as sources_api
from app.api import workspaces as workspaces_api
from app.workers import poller


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _PollSession:
    """Sources first, then the workspace each source asks for, by id."""

    def __init__(self, sources, workspaces):
        self._sources = sources
        self._workspaces = {ws.id: ws for ws in workspaces}
        self._order = [src.workspace_id for src in sources]
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        self.calls += 1
        if self.calls == 1:
            return _Result(self._sources)
        # the loop looks a workspace up per source, in source order
        wanted = self._order.pop(0) if self._order else None
        return _Result(self._workspaces.get(wanted))

    async def commit(self):
        pass


class _ApiSession:
    """Queued rows in order; records writes and the statements it was given."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.added: list = []
        self.statements: list[str] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.statements.append(str(stmt).lower())
        return _Result(self._rows.pop(0) if self._rows else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass

    def touched_sources(self) -> bool:
        return any("update source_configs" in s for s in self.statements)


def _workspace(*, paused: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), name="ws", topic_scope="trading",
        ingestion_paused=paused, payment_enabled=False,
    )


def _source(identifier: str, *, workspace_id: uuid.UUID, is_paused: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), identifier=identifier, source_type="telegram",
        fetch_cadence="hourly", last_fetched_at=None,
        last_fetched_id="telegram:chan:500", workspace_id=workspace_id,
        is_paused=is_paused, paused_at=None,
        connection=SimpleNamespace(status="active"),
    )


@pytest.fixture
def poll(monkeypatch):
    fetched: list[str] = []
    from app.services import billing_service, ingestion_pipeline

    async def _fetch(db, *, source, workspace):
        fetched.append(source.identifier)
        return 1

    async def _no(db, ws_id):
        return False

    monkeypatch.setattr(ingestion_pipeline, "run_fetch_pipeline", _fetch)
    monkeypatch.setattr(billing_service, "is_payment_lapsed", _no)
    monkeypatch.setattr(billing_service, "is_budget_exhausted", _no)

    async def run(sources, workspaces):
        session = _PollSession(sources, workspaces)
        monkeypatch.setattr(poller, "AsyncSessionLocal", lambda: session)
        await poller._poll_all_sources()
        return fetched

    return run


# ── the poll ──────────────────────────────────────────────────────────────────

class TestPollSkipsPausedWorkspaces:
    async def test_skips_the_paused_workspace_and_polls_the_other(self, poll):
        """Both in one tick: one workspace paused, one not."""
        paused_ws, live_ws = _workspace(paused=True), _workspace(paused=False)
        sources = [
            _source("holiday-chan", workspace_id=paused_ws.id),
            _source("live-chan", workspace_id=live_ws.id),
        ]

        fetched = await poll(sources, [paused_ws, live_ws])

        assert fetched == ["live-chan"]

    async def test_skips_every_source_in_the_paused_workspace(self, poll):
        paused_ws = _workspace(paused=True)
        sources = [
            _source("chan-a", workspace_id=paused_ws.id),
            _source("chan-b", workspace_id=paused_ws.id),
        ]

        fetched = await poll(sources, [paused_ws])

        assert fetched == []


# ── the switch leaves everything underneath it alone ──────────────────────────

class TestSwitchKeepsPerSourceState:
    async def _toggle(self, workspace, paused: bool):
        db = _ApiSession([object(), workspace])      # membership row, then workspace
        await workspaces_api.set_ingestion_paused(
            workspace_id=workspace.id,
            payload=workspaces_api.IngestionPauseRequest(paused=paused),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=db,
        )
        return db

    async def test_a_round_trip_leaves_per_source_states_untouched(self):
        """
        The point of the ticket: a source the user paused by hand must still be
        paused after the workspace is switched off and on again.
        """
        ws = _workspace(paused=False)
        by_hand = _source("paused-by-hand", workspace_id=ws.id, is_paused=True)
        running = _source("running", workspace_id=ws.id, is_paused=False)
        cursors = (by_hand.last_fetched_id, running.last_fetched_id)

        db = await self._toggle(ws, True)
        assert ws.ingestion_paused is True
        assert not db.touched_sources()

        db = await self._toggle(ws, False)
        assert ws.ingestion_paused is False
        assert not db.touched_sources()

        assert by_hand.is_paused is True             # still paused on its own
        assert running.is_paused is False
        assert (by_hand.last_fetched_id, running.last_fetched_id) == cursors


# ── on-demand work refuses while the workspace is paused ──────────────────────

class TestPausedWorkspaceRejectsOnDemandWork:
    async def test_fetch_now_is_rejected_with_409(self):
        ws = _workspace(paused=True)
        source = _source("chan", workspace_id=ws.id)
        db = _ApiSession([object(), ws, source])
        tasks = BackgroundTasks()

        with pytest.raises(HTTPException) as exc:
            await sources_api.trigger_fetch(
                workspace_id=ws.id, source_id=source.id, background_tasks=tasks,
                current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
            )

        assert exc.value.status_code == 409
        assert tasks.tasks == []

    async def test_backfill_is_rejected_with_409(self):
        ws = _workspace(paused=True)
        source = _source("chan", workspace_id=ws.id)
        db = _ApiSession([object(), ws, source])

        with pytest.raises(HTTPException) as exc:
            await sources_api.trigger_backfill(
                workspace_id=ws.id, source_id=source.id,
                payload=sources_api.BackfillRequest(
                    date_range_start=datetime(2026, 9, 1), date_range_end=None,
                ),
                background_tasks=BackgroundTasks(),
                current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
            )

        assert exc.value.status_code == 409
        assert db.added == []

    async def test_a_backfill_already_queued_is_cancelled(self, monkeypatch):
        """Queued before the switch, still queued when it lands (#10's lesson)."""
        ws = _workspace(paused=True)
        source = _source("chan", workspace_id=ws.id)
        job = SimpleNamespace(
            id=uuid.uuid4(), source_config_id=source.id, workspace_id=ws.id,
            status="queued", items_ingested=0,
            date_range_start=datetime(2026, 9, 1), date_range_end=None,
            completed_at=None, error_message=None,
        )
        db = _ApiSession([job, source, ws])
        monkeypatch.setattr(sources_api, "AsyncSessionLocal", lambda: db)

        built: list = []
        from app.services import ingestion_pipeline
        monkeypatch.setattr(
            ingestion_pipeline, "_build_connector", lambda src: built.append(src) or src
        )

        await sources_api._run_backfill_bg(job_id=job.id)

        assert built == []
        assert job.status == "cancelled"
        assert "paused" in (job.error_message or "").lower()
