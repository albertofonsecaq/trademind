"""
Periodic Telegram polling worker.
Runs inside the FastAPI process via APScheduler.
Checks every source_config each minute and fetches new messages if its
fetch_cadence interval has elapsed. Sources the user has paused are skipped, as
are whole workspaces with ingestion paused, and workspaces whose payment has
lapsed or whose budget is exhausted.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import NamedTuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.clock import now_utc
from app.core.database import AsyncSessionLocal
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace
from app.services.ingestion_gate import blocked_reason, connection_blocked_reason

log = logging.getLogger(__name__)


class _PlannedSource(NamedTuple):
    """
    One source plus every attribute this tick needs, read before any fetching
    starts: the pipeline shares our session, and re-reading an expired attribute
    mid-loop raises MissingGreenlet, killing the tick and starving every later
    source (#2).
    """
    source: SourceConfig
    source_type: str
    fetch_cadence: str
    last_fetched_at: datetime | None
    identifier: str
    workspace_id: uuid.UUID
    connection_status: str | None
    is_paused: bool


_CADENCE_MAP = {
    "realtime": timedelta(minutes=1),
    "hourly": timedelta(hours=1),
    "daily": timedelta(hours=24),
}


async def _poll_all_sources():
    from app.services.ingestion_pipeline import run_fetch_pipeline

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SourceConfig)
            .options(selectinload(SourceConfig.connection))
        )
        sources = result.scalars().all()

        now = now_utc()
        plan = [
            _PlannedSource(
                source=src,
                source_type=src.source_type,
                fetch_cadence=src.fetch_cadence,
                last_fetched_at=src.last_fetched_at,
                identifier=src.identifier,
                workspace_id=src.workspace_id,
                connection_status=(src.connection.status if src.connection else None),
                is_paused=src.is_paused,
            )
            for src in sources
        ]

        for planned in plan:
            identifier, workspace_id = planned.identifier, planned.workspace_id

            # Gate 0: the user's own pause, source-level — checked first, so a
            # paused source costs nothing: no connector, no workspace lookup, no
            # relevance or distillation spend. Its fetch cursor is left
            # untouched, so resuming continues where it stopped (#10). The
            # workspace-level half of the question needs the workspace row, so
            # it is asked below, still before any spend (#13).
            if blocked_reason(source_paused=planned.is_paused, workspace_paused=False):
                log.debug("Skipping source %s: paused", identifier)
                continue

            # Gate 0c: the account this source fetches through. The same
            # predicate the on-demand endpoints use, so a source the poll skips
            # is one Fetch now refuses rather than accepts and drops (#49).
            reason = connection_blocked_reason(
                source_type=planned.source_type,
                connection_status=planned.connection_status,
            )
            if reason:
                log.debug("Skipping source %s: %s", identifier, reason)
                continue

            # YouTube needs no connection, but it does need the server's key.
            if planned.source_type == "youtube":
                from app.core.config import settings as _s
                if not _s.YOUTUBE_API_KEY:
                    continue

            cadence = _CADENCE_MAP.get(planned.fetch_cadence, timedelta(hours=1))
            if planned.last_fetched_at and (now - planned.last_fetched_at) < cadence:
                continue

            ws_result = await db.execute(
                select(Workspace).where(Workspace.id == workspace_id)
            )
            workspace = ws_result.scalar_one_or_none()
            if not workspace:
                continue

            # Gate 0b: the whole workspace is paused — one switch for a holiday
            # or a spend freeze. Still before any connector or LLM call, and it
            # never touches a source's own paused state, so switching back on
            # resumes exactly the sources that were running before (#13).
            reason = blocked_reason(
                source_paused=planned.is_paused,
                workspace_paused=workspace.ingestion_paused,
            )
            if reason:
                log.debug("Skipping source %s: %s", identifier, reason)
                continue

            # Gate 1: payment lapsed — skip if workspace has payment enforcement
            # but no valid subscription. Already-ingested content stays readable.
            from app.services.billing_service import is_payment_lapsed, is_budget_exhausted
            if await is_payment_lapsed(db, workspace_id):
                log.debug("Skipping source %s: payment lapsed for workspace %s",
                          identifier, workspace_id)
                continue

            # Gate 2: budget exhausted — pause deferrable ingestion jobs.
            # The Ask view is never paused here; this only affects background fetching.
            if await is_budget_exhausted(db, workspace_id):
                log.debug("Skipping source %s: budget cap reached for workspace %s",
                          identifier, workspace_id)
                continue

            try:
                outcome = await run_fetch_pipeline(db, source=planned.source, workspace=workspace)
                if outcome.ingested or outcome.failed:
                    log.info(
                        "Source %s: %d ingested, %d failed",
                        identifier, outcome.ingested, outcome.failed,
                    )
            except Exception as e:
                # Includes the failures a connector or provider cannot recover
                # from, which now abort the source's tick rather than being
                # retried per message for the rest of the range (#9).
                log.error("Poller error for source %s: %s", identifier, e)


async def _mine_all_workspaces():
    """Weekly pattern mining across all workspaces."""
    import logging
    log = logging.getLogger(__name__)
    from app.services.pattern_mining_service import run_pattern_mining
    from app.models.workspace import Workspace

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Workspace))
        workspaces = result.scalars().all()
        from app.services.billing_service import is_payment_lapsed, is_budget_exhausted
        for ws in workspaces:
            if await is_payment_lapsed(db, ws.id) or await is_budget_exhausted(db, ws.id):
                log.debug("Skipping mining for workspace %s: payment lapsed or budget exhausted", ws.id)
                continue
            try:
                count = await run_pattern_mining(db, ws.id)
                if count:
                    log.info("Mining: %d cards touched for workspace %s", count, ws.id)
            except Exception as e:
                log.error("Mining failed for workspace %s: %s", ws.id, e)


async def _validate_all_workspaces():
    """Weekly validation run — after mining, update win rates for all strategy cards."""
    import logging
    log = logging.getLogger(__name__)
    from app.services.validation_service import run_validation
    from app.models.workspace import Workspace

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Workspace))
        workspaces = result.scalars().all()
        for ws in workspaces:
            try:
                count = await run_validation(db, ws.id)
                if count:
                    log.info("Validation: %d cards checked for workspace %s", count, ws.id)
            except Exception as e:
                log.error("Validation failed for workspace %s: %s", ws.id, e)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_poll_all_sources, "interval", minutes=1, id="telegram_poller")
    # Sunday 03:00 UTC: mine patterns, then validate outcomes 30 min later
    scheduler.add_job(_mine_all_workspaces,     "cron", day_of_week="sun", hour=3, minute=0,  id="pattern_miner")
    scheduler.add_job(_validate_all_workspaces, "cron", day_of_week="sun", hour=3, minute=30, id="outcome_validator")
    return scheduler
