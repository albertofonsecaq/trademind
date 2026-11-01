"""
The record of messages that failed, so they are retried instead of lost (#96, ADR 0005).

Only bookkeeping lives here; fetching a message again and processing it is the
pipeline's job, which calls these as it goes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.llm_errors import safe_failure_summary
from app.core.content_unavailable import ContentUnavailable
from app.core.platform_refusal import PlatformRefused
from app.models.failed_message import MAX_ATTEMPTS, FailedMessage

# A database error carries its whole statement and parameters — 6 KB of junk
# for one of #94's links — and a person needs only the start of it.
_MAX_ERROR_CHARS = 500

# The platform handed nothing back for it: deleted there, or a part of a kind the
# source's content filters no longer ask for. Either way there is nothing to retry.
GONE_FROM_SOURCE = (
    "The source no longer offers this message (deleted, or its kind is now "
    "filtered out), so it cannot be retried."
)


async def record_failure(db: AsyncSession, msg: RawMessage, exc: BaseException) -> FailedMessage:
    """
    Record a failed attempt at this message: a first one, or another one.

    A platform's refusal is recorded without counting as an attempt: it was not
    the message's doing, and it is waited out rather than given up (ADR 0007).
    """
    row = await _find(db, msg.source_config_id, msg.stable_id)
    if row is None:
        refused = isinstance(exc, PlatformRefused)
        row = FailedMessage(
            workspace_id=msg.workspace_id,
            source_config_id=msg.source_config_id,
            stable_id=msg.stable_id,
            message_at=msg.timestamp,
            error=_summary(exc),
            attempts=0 if refused else 1,
            retry_after=exc.until if refused else _retry_after(exc, 1),
        )
        try:
            # A savepoint: a poll and a backfill of the same source can both get
            # here first, and the loser must not abort the session it shares
            # with the loop that called it.
            async with db.begin_nested():
                db.add(row)
        except IntegrityError:
            return await count_attempt(db, await _find(db, msg.source_config_id, msg.stable_id), exc)
        await db.commit()
        return row
    return await count_attempt(db, row, exc)


async def count_attempt(db: AsyncSession, row: FailedMessage, exc: BaseException) -> FailedMessage:
    """
    Another failed attempt at a recorded message; the third gives it up.

    A platform's refusal does not count: the message waits until it ends.
    """
    row.error = _summary(exc)
    row.last_failed_at = now_utc()
    if isinstance(exc, PlatformRefused):
        row.retry_after = exc.until
        await db.commit()
        return row
    row.attempts += 1
    row.retry_after = _retry_after(exc, row.attempts)
    if row.attempts >= MAX_ATTEMPTS:
        row.given_up_at = now_utc()
    await db.commit()
    return row


async def give_up(db: AsyncSession, row: FailedMessage, reason: str) -> None:
    """
    Stop retrying at once, without spending the attempts left: the reason
    replaces the last failure's, because it is now the one that matters.
    """
    row.error = reason
    row.given_up_at = now_utc()
    await db.commit()


async def send_back(db: AsyncSession, row: FailedMessage) -> FailedMessage:
    """
    A person sends a given-up message back: the next fetch retries it, with a
    fresh set of attempts (#102). The last reason stays until an attempt
    replaces it. One still being retried is left as it is.
    """
    if row.given_up_at is not None:
        row.given_up_at = None
        row.attempts = 0
        await db.commit()
    return row


async def send_back_given_up(db: AsyncSession, source_config_id: uuid.UUID) -> int:
    """Every given-up message of a source, sent back at once — after an outage, say."""
    result = await db.execute(
        update(FailedMessage)
        .where(FailedMessage.source_config_id == source_config_id, FailedMessage.given_up_at.is_not(None))
        .values(given_up_at=None, attempts=0)
    )
    await db.commit()
    return result.rowcount


async def resolve(db: AsyncSession, row: FailedMessage) -> None:
    """The message went through: there is nothing left to remember."""
    await db.delete(row)
    await db.commit()


async def forget(db: AsyncSession, source_config_id: uuid.UUID, stable_id: str) -> None:
    """
    A message just stored needs no record, whatever its state, and neither does
    the whole it is a part of: a video recorded as failed is stored as its
    transcript's chunks, whose ids extend its own (#126). The caller commits.
    """
    await db.execute(
        delete(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.stable_id.in_(_itself_and_its_wholes(stable_id)),
        )
    )


async def to_retry(db: AsyncSession, source_config_id: uuid.UUID) -> list[FailedMessage]:
    """A source's failures still being retried and not waiting out a refusal, oldest first."""
    rows = await db.execute(
        select(FailedMessage)
        .where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.given_up_at.is_(None),
            or_(FailedMessage.retry_after.is_(None), FailedMessage.retry_after <= now_utc()),
        )
        .order_by(FailedMessage.first_failed_at, FailedMessage.stable_id)
    )
    return list(rows.scalars())


async def _find(db: AsyncSession, source_config_id: uuid.UUID, stable_id: str) -> FailedMessage | None:
    return (await db.execute(
        select(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.stable_id == stable_id,
        )
    )).scalar_one_or_none()


def _itself_and_its_wholes(stable_id: str) -> list[str]:
    """"youtube:v:chunk_0" → it, "youtube:v" and "youtube": every id it extends."""
    parts = stable_id.split(":")
    return [":".join(parts[:n]) for n in range(len(parts), 0, -1)]


def _retry_after(exc: BaseException, attempts: int):
    """When a failure that has now had this many attempts may be retried: None, at once."""
    gap = exc.wait_after(attempts) if isinstance(exc, ContentUnavailable) else None
    return now_utc() + gap if gap else None


def _summary(exc: BaseException) -> str:
    return safe_failure_summary(exc)[:_MAX_ERROR_CHARS]
