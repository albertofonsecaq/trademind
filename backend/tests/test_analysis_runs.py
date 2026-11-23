"""
A mining or validation run is recorded, and so is how it ended — #86.

Both ran as fire-and-forget background tasks. The request answered "started"
with a count hard-coded to 0, the Library reloaded the cards a fixed few
seconds later, and how the run went existed only in the server log:

- a mining run whose every call failed with `401 invalid x-api-key` looked
  exactly like one that found nothing — an empty library and "0";
- a healthy run of 22 clusters takes about five minutes and commits at the end,
  so the reload always showed the old state and the run looked broken.

The run is now a row: running until it ends, then completed, partial or failed,
with what it did and what went wrong. The database refuses a second running
run of the same kind for a workspace.
"""
import uuid

import pytest

from app.models.analysis_run import COMPLETED, FAILED, MINING, PARTIAL, RUNNING, VALIDATION, RunOutcome
from app.services.analysis_run_service import (
    RunAlreadyGoing,
    close_interrupted_runs,
    finish_run,
    latest_runs,
    start_run,
)
from tests.conftest import make_workspace


class TestHowARunEnded:
    def test_nothing_failed_is_completed(self):
        assert RunOutcome(cards_created=2, cards_updated=3).status == COMPLETED

    def test_nothing_to_do_is_still_completed(self):
        assert RunOutcome().status == COMPLETED

    def test_some_clusters_failing_is_partial(self):
        outcome = RunOutcome(cards_created=2, failures=[{"subject": "BTCUSD/wedge", "reason": "cut off"}])

        assert outcome.status == PARTIAL

    def test_every_cluster_failing_is_still_partial_not_failed(self):
        """Failed is for a run that stopped; one that tried everything is partial."""
        outcome = RunOutcome(failures=[{"subject": "BTCUSD/wedge", "reason": "cut off"}])

        assert outcome.status == PARTIAL

    def test_a_run_that_stopped_is_failed(self):
        outcome = RunOutcome(cards_created=1, stopped_because="Anthropic rejected the API key")

        assert outcome.status == FAILED


@pytest.mark.db
class TestRecordingARun:
    async def test_a_started_run_is_running(self, db_session):
        workspace = await make_workspace(db_session)

        run = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        assert run.status == RUNNING
        assert run.finished_at is None
        assert (await latest_runs(db_session, workspace.id))[MINING].id == run.id

    async def test_a_second_run_of_the_same_kind_is_refused(self, db_session):
        workspace = await make_workspace(db_session)
        await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        with pytest.raises(RunAlreadyGoing):
            await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

    async def test_a_refused_run_leaves_the_session_usable(self, db_session):
        workspace = await make_workspace(db_session)
        first = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)
        with pytest.raises(RunAlreadyGoing):
            await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        assert (await latest_runs(db_session, workspace.id))[MINING].id == first.id

    async def test_the_other_kind_waits_too(self, db_session):
        """
        Mining and validation both write a workspace's strategy cards — the
        confidence tier, for one — and mining holds its writes for minutes
        before committing, so one would overwrite the other.
        """
        workspace = await make_workspace(db_session)
        await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        with pytest.raises(RunAlreadyGoing) as refused:
            await start_run(db_session, workspace.id, VALIDATION, workspace.owner_user_id)

        assert refused.value.running_kind == MINING

    async def test_other_workspaces_are_not_blocked(self, db_session):
        workspace = await make_workspace(db_session)
        other = await make_workspace(db_session)
        await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        await start_run(db_session, other.id, MINING, other.owner_user_id)

    async def test_a_finished_run_can_be_followed_by_another(self, db_session):
        workspace = await make_workspace(db_session)
        run = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)
        await finish_run(db_session, run.id, RunOutcome())

        again = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        assert (await latest_runs(db_session, workspace.id))[MINING].id == again.id

    async def test_the_outcome_is_recorded(self, db_session):
        workspace = await make_workspace(db_session)
        run = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)
        failures = [{"subject": "BTCUSD/wedge", "reason": "reply was cut off"}]

        await finish_run(db_session, run.id, RunOutcome(cards_created=2, cards_updated=19, failures=failures))

        recorded = (await latest_runs(db_session, workspace.id))[MINING]
        assert (recorded.status, recorded.cards_created, recorded.cards_updated) == (PARTIAL, 2, 19)
        assert recorded.failures == failures
        assert recorded.finished_at is not None

    async def test_a_scheduled_run_has_no_user(self, db_session):
        workspace = await make_workspace(db_session)

        run = await start_run(db_session, workspace.id, VALIDATION, None)

        assert run.triggered_by_user_id is None

    async def test_no_run_yet_is_none(self, db_session):
        workspace = await make_workspace(db_session)

        assert await latest_runs(db_session, workspace.id) == {MINING: None, VALIDATION: None}


@pytest.mark.db
class TestARunThatNeverRecordedItsEnd:
    async def test_stops_blocking_once_it_is_older_than_any_real_run(self, db_session):
        """
        If recording how a run ended fails, its row says running for good. A
        mining run takes minutes; one still running hours later is not.
        """
        from datetime import timedelta

        from app.core.clock import now_utc
        from app.services.analysis_run_service import ABANDONED_AFTER

        workspace = await make_workspace(db_session)
        stuck = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)
        stuck.started_at = now_utc() - ABANDONED_AFTER - timedelta(minutes=1)
        await db_session.flush()

        fresh = await start_run(db_session, workspace.id, VALIDATION, workspace.owner_user_id)

        await db_session.refresh(stuck)
        assert stuck.status == FAILED
        assert "never recorded" in stuck.error_message
        assert fresh.status == RUNNING

    async def test_a_recent_run_still_blocks(self, db_session):
        workspace = await make_workspace(db_session)
        await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        with pytest.raises(RunAlreadyGoing):
            await start_run(db_session, workspace.id, VALIDATION, workspace.owner_user_id)


@pytest.mark.db
class TestARunTheServerNeverFinished:
    async def test_is_closed_as_failed_and_stops_blocking(self, db_session):
        """
        A run is a background task in the server process. A restart kills it
        mid-run, and a row left `running` would refuse every run after it.
        """
        workspace = await make_workspace(db_session)
        stuck = await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)

        assert await close_interrupted_runs(db_session) >= 1

        closed = (await latest_runs(db_session, workspace.id))[MINING]
        assert closed.id == stuck.id
        assert closed.status == FAILED
        assert "restarted" in closed.error_message
        await start_run(db_session, workspace.id, MINING, workspace.owner_user_id)
