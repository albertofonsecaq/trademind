"""
Resuming a paused source.

A source paused for an hour and one paused for three weeks cost very different
amounts to catch up on, so the user picks how to resume and the mode travels as
part of the request — never inferred, never defaulted (#12).

Neither mode removes anything. The fetch cursor is the only thing that can move,
and it only ever moves forward:

- CATCH_UP leaves it alone, so the next poll ingests the backlog from exactly
  where the pause stopped, still subject to the relevance check and the payment
  and budget gates.
- FROM_NOW sets it to the source's newest item, so the next poll starts after
  that. The paused window is declined, not deleted: every piece of evidence the
  workspace already holds stays exactly as it is.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_config import SourceConfig
from app.services.ingestion_pipeline import _build_connector

log = logging.getLogger(__name__)

CATCH_UP = "catch_up"
FROM_NOW = "from_now"
RESUME_MODES = (CATCH_UP, FROM_NOW)


class LatestItemUnknown(Exception):
    """
    The connector could not name the source's newest item, so there is nothing
    safe to move the cursor to. Clearing it instead would re-ingest the source's
    entire history, which is the opposite of what FROM_NOW was asked to do.
    """


async def resume_source(db: AsyncSession, *, source: SourceConfig, mode: str) -> None:
    """Clear the paused state, moving the cursor only if the mode says to."""
    if mode == FROM_NOW:
        connector = _build_connector(source)
        latest = await connector.latest_stable_id()
        if not latest:
            raise LatestItemUnknown(source.identifier)
        log.info(
            "Resuming source %s from now: cursor %s → %s",
            source.identifier, source.last_fetched_id, latest,
        )
        source.last_fetched_id = latest

    source.is_paused = False
    source.paused_at = None
