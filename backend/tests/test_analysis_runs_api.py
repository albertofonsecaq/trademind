"""
Starting a run, and reading how the latest one went, through the API — #86.

Starting mining or validation answers with the run it started, which the
Library then follows; a second start while one is going is refused, so two runs
never write the same strategy cards at once. Every member of the workspace sees
the same runs; anyone else is refused.
"""
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import analysis_runs, strategy_cards, validation
from app.models.analysis_run import MINING, RUNNING, VALIDATION, RunOutcome
from app.models.workspace_member import WorkspaceMember
from app.services import analysis_run_service
from tests.conftest import make_user, make_workspace

pytestmark = pytest.mark.db


async def _member(session, workspace, user=None):
    user = user or await make_user(session)
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="member"))
    await session.flush()
    return user


@pytest.fixture
def scheduled(monkeypatch):
    """What the request hands to the background, without running it."""
    queued = []
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, fn, *a, **kw: queued.append((fn, a, kw)))
    return queued


class TestStartingARun:
    @pytest.mark.parametrize("start, kind", [
        (strategy_cards.trigger_mining, MINING),
        (validation.trigger_validation, VALIDATION),
    ])
    async def test_answers_with_the_run_and_hands_it_to_the_background(
        self, db_session, scheduled, start, kind,
    ):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)

        run = await start(workspace.id, BackgroundTasks(), user, db_session)

        assert (run.kind, run.status, run.triggered_by_user_id) == (kind, RUNNING, user.id)
        [(fn, args, kwargs)] = scheduled
        assert fn is analysis_run_service.carry_out
        assert kwargs == {"run_id": run.id, "workspace_id": workspace.id, "kind": kind}

    async def test_a_second_start_while_one_is_going_is_refused(self, db_session, scheduled):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        await strategy_cards.trigger_mining(workspace.id, BackgroundTasks(), user, db_session)

        with pytest.raises(HTTPException) as refused:
            await strategy_cards.trigger_mining(workspace.id, BackgroundTasks(), user, db_session)

        assert refused.value.status_code == 409
        assert len(scheduled) == 1

    async def test_validation_waits_for_a_running_mining_run_and_says_which(self, db_session, scheduled):
        workspace = await make_workspace(db_session)
        user = await _member(db_session, workspace)
        await strategy_cards.trigger_mining(workspace.id, BackgroundTasks(), user, db_session)

        with pytest.raises(HTTPException) as refused:
            await validation.trigger_validation(workspace.id, BackgroundTasks(), user, db_session)

        assert refused.value.status_code == 409
        assert "Pattern mining" in refused.value.detail

    async def test_someone_outside_the_workspace_cannot_start_one(self, db_session, scheduled):
        workspace = await make_workspace(db_session)
        outsider = await make_user(db_session)

        with pytest.raises(HTTPException) as refused:
            await strategy_cards.trigger_mining(workspace.id, BackgroundTasks(), outsider, db_session)

        assert refused.value.status_code == 403
        assert scheduled == []


class TestReadingTheLatestRuns:
    async def test_every_member_sees_the_same_runs(self, db_session, scheduled):
        workspace = await make_workspace(db_session)
        starter = await _member(db_session, workspace)
        colleague = await _member(db_session, workspace)
        run = await strategy_cards.trigger_mining(workspace.id, BackgroundTasks(), starter, db_session)

        latest = await analysis_runs.latest(workspace.id, colleague, db_session)

        assert latest.mining.id == run.id
        assert latest.validation is None

    async def test_someone_outside_the_workspace_cannot_read_them(self, db_session):
        workspace = await make_workspace(db_session)
        outsider = await make_user(db_session)

        with pytest.raises(HTTPException) as refused:
            await analysis_runs.latest(workspace.id, outsider, db_session)

        assert refused.value.status_code == 403


class TestCarryingOutARun:
    async def test_the_outcome_is_recorded_when_it_ends(self, db_session, monkeypatch):
        workspace = await make_workspace(db_session)
        run = await analysis_run_service.start_run(db_session, workspace.id, MINING, None)
        _share_the_test_session(monkeypatch, db_session)

        async def _mine(db, workspace_id):
            return RunOutcome(cards_created=2, cards_updated=1)

        monkeypatch.setattr("app.services.pattern_mining_service.run_pattern_mining", _mine)

        await analysis_run_service.carry_out(run_id=run.id, workspace_id=workspace.id, kind=MINING)

        await db_session.refresh(run)
        assert (run.status, run.cards_created, run.cards_updated) == ("completed", 2, 1)

    async def test_a_run_that_crashes_is_recorded_as_failed(self, db_session, monkeypatch):
        workspace = await make_workspace(db_session)
        run = await analysis_run_service.start_run(db_session, workspace.id, VALIDATION, None)
        _share_the_test_session(monkeypatch, db_session)

        async def _crash(db, workspace_id):
            raise RuntimeError("the database went away")

        monkeypatch.setattr("app.services.validation_service.run_validation", _crash)

        await analysis_run_service.carry_out(run_id=run.id, workspace_id=workspace.id, kind=VALIDATION)

        await db_session.refresh(run)
        assert run.status == "failed"
        assert "the database went away" in run.error_message


def _share_the_test_session(monkeypatch, session):
    """carry_out opens its own sessions; here they are the test's, so it rolls back."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session():
        yield session

    monkeypatch.setattr(analysis_run_service, "AsyncSessionLocal", _session)
