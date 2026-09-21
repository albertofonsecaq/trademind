"""
Periodic Telegram polling worker.
Runs inside the FastAPI process via APScheduler.
Checks all active source_configs every minute and fetches new messages
if their fetch_cadence interval has elapsed.
"""
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace

log = logging.getLogger(__name__)

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

        now = datetime.now(timezone.utc)
        # Snapshot what the loop needs before running any source: the pipeline shares
        # this session, and an expired attribute re-read mid-loop raises
        # MissingGreenlet, killing the whole tick and starving every later source (#2).
        plan = [
            (
                src,
                src.source_type,
                src.fetch_cadence,
                src.last_fetched_at,
                src.identifier,
                src.workspace_id,
                (src.connection.status if src.connection else None),
            )
            for src in sources
        ]

        for source, source_type, fetch_cadence, last, identifier, workspace_id, conn_status in plan:
            # Telegram requires an active connection; YouTube only needs YOUTUBE_API_KEY
            if source_type == "telegram":
                if conn_status != "active":
                    continue
            elif source_type == "youtube":
                from app.core.config import settings as _s
                if not _s.YOUTUBE_API_KEY:
                    continue

            cadence = _CADENCE_MAP.get(fetch_cadence, timedelta(hours=1))
            if last and (now - last) < cadence:
                continue

            ws_result = await db.execute(
                select(Workspace).where(Workspace.id == workspace_id)
            )
            workspace = ws_result.scalar_one_or_none()
            if not workspace:
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
                count = await run_fetch_pipeline(db, source=source, workspace=workspace)
                if count:
                    log.info("Fetched %d new items from source %s", count, identifier)
            except Exception as e:
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
