"""
The record of platforms refusing us, shared by every fetch (#130, ADR 0009).

A refusal is waited out (ADR 0007), and how long is decided once, where it
happens. Kept here rather than in the running process, every fetch — the
poller, a manual fetch, a backfill, a retry — and a restarted process ask
before reaching the platform, and none of them asks while it is refusing.

Each call has a session of its own: the callers are deep in a connector or a
transcript fetch, with no session of theirs to lend, and a refusal must be
recorded whatever happens to the fetch that met it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import AsyncSessionLocal
from app.models.platform_refusal import PlatformRefusal

YOUTUBE = "youtube"


async def refused_until(platform: str) -> datetime | None:
    """Until when the platform refused us last; None if it never has."""
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(PlatformRefusal.until).where(PlatformRefusal.platform == platform)
        )).scalar_one_or_none()


async def refuse(platform: str, until: datetime, reason: str) -> datetime:
    """
    Record that the platform is refusing us until `until`, and return until
    when it now is: never earlier than a refusal already recorded, since two
    fetches refused at the same moment each record an end.
    """
    async with AsyncSessionLocal() as db:
        stmt = insert(PlatformRefusal).values(platform=platform, until=until, reason=reason)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PlatformRefusal.platform],
            set_={
                "until": func.greatest(PlatformRefusal.until, stmt.excluded.until),
                "reason": stmt.excluded.reason,
                "refused_at": func.now(),
            },
        ).returning(PlatformRefusal.until)
        recorded = (await db.execute(stmt)).scalar_one()
        await db.commit()
        return recorded
