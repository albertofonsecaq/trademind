"""
The record of messages that failed, so they are retried instead of lost (#96, ADR 0005).

Only bookkeeping lives here; fetching a message again and processing it is the
pipeline's job, which calls these as it goes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.llm_errors import safe_failure_summary
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
    """Record a failed attempt at this message: a first one, or another one."""
    row = await _find(db, msg.source_config_id, msg.stable_id)
    if row is None:
        row = FailedMessage(
            workspace_id=msg.workspace_id,
            source_config_id=msg.source_config_id,
            stable_id=msg.stable_id,
            message_at=msg.timestamp,
            error=_summary(exc),
            attempts=1,
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
    """Another failed attempt at a recorded message; the third gives it up."""
    row.attempts += 1
    row.error = _summary(exc)
    row.last_failed_at = now_utc()
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


async def resolve(db: AsyncSession, row: FailedMessage) -> None:
    """The message went through: there is nothing left to remember."""
    await db.delete(row)
    await db.commit()


async def forget(db: AsyncSession, source_config_id: uuid.UUID, stable_id: str) -> None:
    """A message just stored needs no record, whatever its state. The caller commits."""
    await db.execute(
        delete(FailedMessage).where(
            FailedMessage.source_config_id == source_config_id,
            FailedMessage.stable_id == stable_id,
        )
    )


async def to_retry(db: AsyncSession, source_config_id: uuid.UUID) -> list[FailedMessage]:
    """A source's failures still being retried, oldest first."""
    rows = await db.execute(
        select(FailedMessage)
        .where(FailedMessage.source_config_id == source_config_id, FailedMessage.given_up_at.is_(None))
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


def _summary(exc: BaseException) -> str:
    return safe_failure_summary(exc)[:_MAX_ERROR_CHARS]
