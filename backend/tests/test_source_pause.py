"""
Tests for #10 — a paused source stops being polled, and nothing is lost.

The two halves of the feature both have a way to go quietly wrong:

- the poll could skip a paused source *after* building a connector or consulting
  the billing gates, which still costs network calls and spend;
- pause/resume could be implemented as a reset, rewriting the fetch cursor and
  so re-ingesting (or silently skipping) a window of evidence.

These tests pin both down. Offline by design, like the rest of the suite: the
session, the connectors and the billing gates are all stubbed.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import sources as sources_api
from app.models.backfill_job import BackfillJob
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace
from app.workers import poller
from tests.conftest import FakeSession


def _ingesting_workspace() -> SimpleNamespace:
    """A workspace that is not itself paused — the #13 switch is off."""
    return SimpleNamespace(id=uuid.uuid4(), topic_scope="trading", ingestion_paused=False)


def _source(identifier: str, *, is_paused: bool, workspace_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        identifier=identifier,
        source_type="telegram",
        fetch_cadence="hourly",
        last_fetched_at=None,
        last_fetched_id=None,
        workspace_id=workspace_id,
        is_paused=is_paused,
        connection=SimpleNamespace(status="active"),
    )


# ── the recurring poll ────────────────────────────────────────────────────────

class TestPollSkipsPausedSources:
    async def test_skips_the_paused_source_and_still_polls_its_neighbour(self, poll):
        """One bad apple: a paused source must not stop the rest of the tick."""
        result = await poll.run(
            _source("paused-chan", is_paused=True, workspace_id=poll.workspace_id),
            _source("live-chan", is_paused=False, workspace_id=poll.workspace_id),
        )
        assert result.fetched == ["live-chan"]

    async def test_never_fetches_a_paused_source_at_all(self, poll):
        result = await poll.run(
            _source("paused-chan", is_paused=True, workspace_id=poll.workspace_id),
        )
        assert result.fetched == []

    async def test_skips_before_spending_anything_on_the_source(self, poll):
        """The gate belongs ahead of the billing checks and the connector build."""
        result = await poll.run(
            _source("paused-chan", is_paused=True, workspace_id=poll.workspace_id),
            _source("live-chan", is_paused=False, workspace_id=poll.workspace_id),
        )
        assert len(result.gate_checks) == 1      # only the live source was costed


# ── pause / resume over the API ───────────────────────────────────────────────

class TestPauseKeepsEvidenceAndCursor:
    async def _patch(self, source, **fields):
        user = SimpleNamespace(id=uuid.uuid4())
        db = FakeSession(rows={SourceConfig: source})
        await sources_api.update_source(
            workspace_id=uuid.uuid4(),
            source_id=source.id,
            payload=sources_api.SourceUpdateRequest(**fields),
            current_user=user,
            db=db,
        )
        return db

    async def test_pausing_never_moves_the_fetch_cursor(self):
        source = _source("chan", is_paused=False, workspace_id=uuid.uuid4())
        source.last_fetched_id = "telegram:chan:500"
        source.last_fetched_at = datetime(2026, 10, 16, 12, 0)

        await self._patch(source, is_paused=True)

        assert source.is_paused is True
        assert source.last_fetched_id == "telegram:chan:500"
        assert source.last_fetched_at == datetime(2026, 10, 16, 12, 0)

    async def test_this_field_cannot_resume_a_source(self):
        """
        Resuming moved to its own endpoint in #12, because it needs a mode:
        catch up on the backlog, or skip to the newest item. Unpausing through
        this field would be the silent default that ticket exists to prevent.
        The round trip's effect on the cursor is covered in test_resume_mode.py.
        """
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        source.last_fetched_id = "telegram:chan:500"

        with pytest.raises(HTTPException) as exc:
            await self._patch(source, is_paused=False)

        assert exc.value.status_code == 400
        assert source.is_paused is True
        assert source.last_fetched_id == "telegram:chan:500"

    async def test_editing_other_fields_leaves_the_paused_state_alone(self):
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        await self._patch(source, fetch_cadence="daily")
        assert source.is_paused is True


class TestPausedSourceRejectsFetchAndBackfill:
    async def test_fetch_now_is_rejected_with_409(self):
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        db = FakeSession(rows={Workspace: _ingesting_workspace(), SourceConfig: source})
        tasks = BackgroundTasks()

        with pytest.raises(HTTPException) as exc:
            await sources_api.trigger_fetch(
                workspace_id=uuid.uuid4(),
                source_id=source.id,
                background_tasks=tasks,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                db=db,
            )

        assert exc.value.status_code == 409
        assert tasks.tasks == []                 # nothing was queued to spend

    async def test_backfill_is_rejected_with_409(self):
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        db = FakeSession(rows={Workspace: _ingesting_workspace(), SourceConfig: source})

        with pytest.raises(HTTPException) as exc:
            await sources_api.trigger_backfill(
                workspace_id=uuid.uuid4(),
                source_id=source.id,
                payload=sources_api.BackfillRequest(
                    date_range_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    date_range_end=None,
                ),
                background_tasks=BackgroundTasks(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
                db=db,
            )

        assert exc.value.status_code == 409
        assert db.added == []                    # no job row was created

    async def test_an_unpaused_source_still_accepts_a_fetch(self):
        """The guard must not become a blanket block."""
        source = _source("chan", is_paused=False, workspace_id=uuid.uuid4())
        db = FakeSession(rows={Workspace: _ingesting_workspace(), SourceConfig: source})
        tasks = BackgroundTasks()

        await sources_api.trigger_fetch(
            workspace_id=uuid.uuid4(),
            source_id=source.id,
            background_tasks=tasks,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=db,
        )

        assert len(tasks.tasks) == 1


class TestPauseStopsWorkAlreadyQueued:
    """
    The 409s above only guard the front door. A fetch task scheduled seconds
    before the pause, or a backfill job still queued when it lands, re-reads the
    source in a fresh session — and must find it paused and stop there, before a
    connector is built.
    """

    async def test_a_queued_fetch_stops_once_the_source_is_paused(self, monkeypatch):
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        workspace = SimpleNamespace(topic_scope="trading", ingestion_paused=False)
        db = FakeSession(rows={SourceConfig: source, Workspace: workspace})
        monkeypatch.setattr(sources_api, "AsyncSessionLocal", lambda: db)

        fetched: list[str] = []

        async def _fetch(db, *, source, workspace):
            fetched.append(source.identifier)
            return 1

        from app.services import ingestion_pipeline
        monkeypatch.setattr(ingestion_pipeline, "run_fetch_pipeline", _fetch)

        await sources_api._run_fetch_bg(source_id=source.id, workspace_id=uuid.uuid4())

        assert fetched == []

    async def test_a_queued_backfill_is_cancelled_once_the_source_is_paused(self, monkeypatch):
        source = _source("chan", is_paused=True, workspace_id=uuid.uuid4())
        workspace = SimpleNamespace(topic_scope="trading", ingestion_paused=False)
        job = SimpleNamespace(
            id=uuid.uuid4(),
            source_config_id=source.id,
            workspace_id=uuid.uuid4(),
            status="queued",
            items_ingested=0,
            date_range_start=datetime(2026, 9, 1),
            date_range_end=None,
            completed_at=None,
            error_message=None,
        )
        db = FakeSession(
            rows={BackfillJob: job, SourceConfig: source, Workspace: workspace}
        )
        monkeypatch.setattr(sources_api, "AsyncSessionLocal", lambda: db)

        built: list = []

        from app.services import ingestion_pipeline
        monkeypatch.setattr(
            ingestion_pipeline, "_build_connector", lambda src: built.append(src) or src
        )

        await sources_api._run_backfill_bg(job_id=job.id)

        assert built == []                       # never reached the connector
        assert job.status == "cancelled"
        assert "paused" in (job.error_message or "").lower()
