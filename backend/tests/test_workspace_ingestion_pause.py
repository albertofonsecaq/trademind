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
from app.models.source_config import SourceConfig
from app.schemas.workspace import IngestionPauseRequest
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
    """
    Answers by what the statement asks for, never by call order.

    Positional fakes lie here: the poll skips the workspace lookup entirely for
    a source that is itself paused, or whose cadence has not elapsed, so a
    call-counting stub silently hands back the wrong workspace and the test
    passes for the wrong reason. Looking up the id in the statement's own
    parameters keeps the fake honest, and `workspace_lookups` lets a test prove
    the skip came from the workspace gate rather than from a missing row.
    """

    def __init__(self, sources, workspaces):
        self._sources = sources
        self._workspaces = {ws.id: ws for ws in workspaces}
        self.workspace_lookups: list[uuid.UUID] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is SourceConfig:
            return _Result(self._sources)
        for value in stmt.compile().params.values():
            if value in self._workspaces:
                self.workspace_lookups.append(value)
                return _Result(self._workspaces[value])
        return _Result(None)

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
        """
        Any mention at all, not just an UPDATE: the switch has no business
        reading sources either, and a handler that loaded them could mutate
        them through the ORM without ever issuing an UPDATE of its own.
        """
        return any("source_configs" in s for s in self.statements)


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
        return SimpleNamespace(fetched=fetched, session=session)

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

        result = await poll(sources, [paused_ws, live_ws])

        assert result.fetched == ["live-chan"]

    async def test_skips_every_source_in_the_paused_workspace(self, poll):
        paused_ws = _workspace(paused=True)
        sources = [
            _source("chan-a", workspace_id=paused_ws.id),
            _source("chan-b", workspace_id=paused_ws.id),
        ]

        result = await poll(sources, [paused_ws])

        assert result.fetched == []
        # Both sources reached the workspace gate and were turned back there,
        # rather than falling out earlier on a lookup that returned nothing.
        assert result.session.workspace_lookups == [paused_ws.id, paused_ws.id]


class TestTheTwoSwitchesTogether:
    async def test_a_source_paused_by_hand_stays_paused_when_the_workspace_resumes(self, poll):
        """
        The ticket's premise, in one tick: with the workspace switch off, the
        source the user paused by hand is still skipped while its neighbour runs.
        """
        ws = _workspace(paused=False)
        sources = [
            _source("paused-by-hand", workspace_id=ws.id, is_paused=True),
            _source("running", workspace_id=ws.id),
        ]

        result = await poll(sources, [ws])

        assert result.fetched == ["running"]
        # The paused source never even needed its workspace read.
        assert result.session.workspace_lookups == [ws.id]


# ── the switch leaves everything underneath it alone ──────────────────────────

class TestSwitchKeepsPerSourceState:
    async def _toggle(self, workspace, paused: bool):
        db = _ApiSession([object(), workspace])      # membership row, then workspace
        await workspaces_api.set_ingestion_paused(
            workspace_id=workspace.id,
            payload=IngestionPauseRequest(paused=paused),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            db=db,
        )
        return db

    async def test_a_round_trip_touches_nothing_but_the_workspace_flag(self):
        """
        Per-source state survives because the switch never goes near it. The
        handler is never handed a source, so the honest assertion is about what
        it is allowed to touch: the membership check, the workspace, and nothing
        else. Anything that read or wrote source_configs would fail here.
        """
        ws = _workspace(paused=False)

        db = await self._toggle(ws, True)
        assert ws.ingestion_paused is True
        assert not db.touched_sources()
        assert len(db.statements) == 2                # membership, workspace

        db = await self._toggle(ws, False)
        assert ws.ingestion_paused is False
        assert not db.touched_sources()
        assert len(db.statements) == 2


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
