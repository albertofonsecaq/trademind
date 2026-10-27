"""
The record of messages that failed, so they are retried instead of lost (#96, ADR 0005).

Only bookkeeping lives here; fetching a message again and processing it is the
pipeline's job, which calls these as it goes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.llm_errors import safe_failure_summary
from app.models.failed_message import MAX_ATTEMPTS, FailedMessage

# A database error carries its whole statement and parameters — 6 KB of junk
# for one of #94's links — and a person needs only the start of it.
_MAX_ERROR_CHARS = 500

GONE_FROM_SOURCE = "The message is no longer at the source, so it cannot be retried."


async def record_failure(db: AsyncSession, msg: RawMessage, exc: BaseException) -> FailedMessage:
    """Record a failed attempt at this message: a first one, or another one."""
    row = await _find(db, msg.source_config_id, msg.stable_id)
    error = safe_failure_summary(exc)[:_MAX_ERROR_CHARS]
    if row is None:
        row = FailedMessage(
            workspace_id=msg.workspace_id,
            source_config_id=msg.source_config_id,
            stable_id=msg.stable_id,
            message_at=msg.timestamp,
            error=error,
            attempts=1,
        )
        db.add(row)
    else:
        row.attempts += 1
        row.error = error
        row.last_failed_at = now_utc()
        if row.attempts >= MAX_ATTEMPTS:
            row.given_up_at = now_utc()
    await db.commit()
    return row


async def give_up(db: AsyncSession, row: FailedMessage, reason: str) -> None:
    row.error = reason
    row.given_up_at = now_utc()
    await db.commit()


async def resolve(db: AsyncSession, row: FailedMessage) -> None:
    """The message went through: there is nothing left to remember."""
    await db.delete(row)
    await db.commit()


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
