"""
Recording pattern-mining and validation runs, and how each ended — #86.

A run is started in the request, so a second one can be refused there, and is
carried out in a background task, whose outcome is recorded in a session of its
own: the run's own work rolling back must not take the record of it along.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now_utc
from app.core.database import AsyncSessionLocal
from app.models.analysis_run import (
    FAILED, MINING, RUNNING, VALIDATION, AnalysisRun, RunKind, RunOutcome,
)

log = logging.getLogger(__name__)

_INTERRUPTED = "The server restarted before this run finished."
_ABANDONED = "This run never recorded how it ended, and was closed so others could start."

# Older than any real run: mining a workspace takes minutes. A row still running
# after this is one whose end was never recorded, and must stop blocking.
ABANDONED_AFTER = timedelta(hours=2)


class RunAlreadyGoing(Exception):
    """A run is already going for this workspace; `running_kind` says which."""

    def __init__(self, running_kind: str):
        super().__init__(f"{running_kind} is already running")
        self.running_kind = running_kind


async def start_run(
    db: AsyncSession, workspace_id: uuid.UUID, kind: RunKind, user_id: uuid.UUID | None,
) -> AnalysisRun:
    """Record a run as running, or raise RunAlreadyGoing if the workspace has one going."""
    await db.execute(
        update(AnalysisRun)
        .where(
            AnalysisRun.workspace_id == workspace_id,
            AnalysisRun.status == RUNNING,
            AnalysisRun.started_at < now_utc() - ABANDONED_AFTER,
        )
        .values(status=FAILED, error_message=_ABANDONED, finished_at=now_utc())
    )
    run = AnalysisRun(workspace_id=workspace_id, kind=kind, status=RUNNING, triggered_by_user_id=user_id)
    try:
        # A savepoint, so a refusal leaves the caller's transaction usable.
        async with db.begin_nested():
            db.add(run)
    except IntegrityError as e:
        running = (await db.execute(
            select(AnalysisRun.kind).where(AnalysisRun.workspace_id == workspace_id, AnalysisRun.status == RUNNING)
        )).scalar_one_or_none()
        raise RunAlreadyGoing(running or kind) from e
    await db.commit()
    return run


async def finish_run(db: AsyncSession, run_id: uuid.UUID, outcome: RunOutcome) -> None:
    run = await db.get(AnalysisRun, run_id)
    run.status = outcome.status
    run.cards_created = outcome.cards_created
    run.cards_updated = outcome.cards_updated
    run.failures = outcome.failures
    run.error_message = outcome.stopped_because
    run.finished_at = now_utc()
    await db.commit()


async def carry_out(*, run_id: uuid.UUID, workspace_id: uuid.UUID, kind: RunKind) -> None:
    """
    Do the work of a started run, then record how it ended. A crash is an
    outcome too: recorded as failed, with its reason, and logged with its
    traceback.
    """
    # Looked up at call time rather than imported at the top, so the run does
    # whatever those modules hold now — what lets a test stand in for them.
    from app.services.pattern_mining_service import run_pattern_mining
    from app.services.validation_service import run_validation

    work = {MINING: run_pattern_mining, VALIDATION: run_validation}[kind]
    try:
        async with AsyncSessionLocal() as db:
            outcome = await work(db, workspace_id)
    except Exception as e:
        log.exception("Background %s failed for %s", kind, workspace_id)
        outcome = RunOutcome(stopped_because=f"The run crashed: {e}")

    # A session of its own: the run's work rolling back must not take the
    # record of it along.
    async with AsyncSessionLocal() as db:
        await finish_run(db, run_id, outcome)


async def latest_runs(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, AnalysisRun | None]:
    """The newest run of each kind for a workspace, or None where there has been none."""
    latest = {}
    for kind in (MINING, VALIDATION):
        latest[kind] = (await db.execute(
            select(AnalysisRun)
            .where(AnalysisRun.workspace_id == workspace_id, AnalysisRun.kind == kind)
            .order_by(AnalysisRun.started_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    return latest


async def close_interrupted_runs(db: AsyncSession) -> int:
    """
    Close every run still marked running, as failed. Called at start-up: a run
    is carried out inside the server process, so at start-up none can really be
    going, and a row left running would refuse every later run of its kind.
    """
    result = await db.execute(
        update(AnalysisRun)
        .where(AnalysisRun.status == RUNNING)
        .values(status=FAILED, error_message=_INTERRUPTED, finished_at=now_utc())
    )
    await db.commit()
    if result.rowcount:
        log.warning("Closed %d analysis run(s) the server never finished", result.rowcount)
    return result.rowcount
