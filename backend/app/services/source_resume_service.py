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
from datetime import datetime
from typing import Literal

from app.core.clock import now_utc
from app.models.source_config import SourceConfig
from app.services.ingestion_pipeline import _build_connector

log = logging.getLogger(__name__)

CATCH_UP = "catch_up"
FROM_NOW = "from_now"
# One definition of the set, imported by the API so the two cannot drift.
ResumeMode = Literal["catch_up", "from_now"]


class NewestEvidenceUnknown(Exception):
    """
    We could not learn the source's newest item — the connector said nothing, or
    could not be reached at all. There is then nothing safe to move the cursor
    to: clearing it would re-ingest the source's entire history, the opposite of
    what FROM_NOW asked for. Callers leave the source paused.
    """


def _telegram_message_id(stable_id: str | None) -> int | None:
    """The numeric id inside a telegram cursor, when it is one."""
    if not stable_id or not stable_id.startswith("telegram:"):
        return None
    parts = stable_id.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _moves_backwards(current: str | None, latest: str) -> bool:
    """
    True when the 'newest' item is actually behind the cursor we already hold.

    It can happen: the head of a Telegram channel gets deleted, or a backfill
    left a cursor ahead of what the channel will now report. Writing it anyway
    would re-walk the very window the user just declined, so we keep the cursor
    instead. Only telegram ids are comparable; for anything else we trust the
    connector's "newest".
    """
    current_id = _telegram_message_id(current)
    latest_id = _telegram_message_id(latest)
    if current_id is None or latest_id is None:
        return False
    return latest_id <= current_id


async def resume_source(*, source: SourceConfig, mode: ResumeMode) -> None:
    """Clear the paused state, moving the cursor only if the mode says to."""
    if mode == FROM_NOW:
        try:
            connector = _build_connector(source)
            latest = await connector.latest_stable_id()
        except Exception as e:
            # A revoked connection or an unreachable platform is not a reason to
            # guess a cursor, and silently catching up would spend the money the
            # user just declined. Stay paused and say so.
            log.warning(
                "Cannot read newest item for source %s: %s", source.identifier, e, exc_info=True
            )
            raise NewestEvidenceUnknown(source.identifier) from e
        if not latest:
            raise NewestEvidenceUnknown(source.identifier)

        if _moves_backwards(source.last_fetched_id, latest):
            log.info(
                "Resuming source %s from now: keeping cursor %s, newest %s is not ahead of it",
                source.identifier, source.last_fetched_id, latest,
            )
        else:
            log.info(
                "Resuming source %s from now: cursor %s → %s",
                source.identifier, source.last_fetched_id, latest,
            )
            source.last_fetched_id = latest

        # The position now sits at the newest content, so nothing published
        # before this moment is pending any more. Both timestamps are written:
        #
        # last_fetched_at is what the resume prompt measures the backlog from,
        # so leaving it stale would re-offer the window just declined — and it
        # spaces the next poll by one cadence, right when there is nothing new.
        #
        # last_fetched_item_at is the declined window's far edge. Without it a
        # connector that later loses this marker — a deleted video — would fall
        # back to the newest *stored* evidence, behind the decline, and re-fetch
        # and re-pay transcription for exactly what the user chose to skip (#19).
        declined_at = now_utc()
        source.last_fetched_at = declined_at
        source.last_fetched_item_at = declined_at

    source.is_paused = False
    source.paused_at = None
