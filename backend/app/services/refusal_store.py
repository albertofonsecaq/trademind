"""
The record of platforms refusing us, shared by every fetch (#130, ADR 0009).

A refusal is waited out (ADR 0007). How long is `escalate`'s arithmetic over
the refusal recorded before it (#140, ADR 0010), which is why the row keeps how
many refusals in a row there have been as well as until when: the length of the
next wait is not a constant, and nothing but this row remembers the last one.

Kept here rather than in the running process, every fetch — the poller, a
manual fetch, a backfill, a retry — and a restarted process ask before reaching
the platform, and none of them asks while it is refusing.

Each call has a session of its own: the callers are deep in a connector or a
transcript fetch, with no session of theirs to lend, and a refusal must be
recorded whatever happens to the fetch that met it.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import now_utc
from app.core.database import AsyncSessionLocal
from app.core.platform_refusal import Cooldown, escalate
from app.models.platform_refusal import PlatformRefusal

YOUTUBE = "youtube"


async def current(platform: str) -> Cooldown | None:
    """
    The cool-down recorded for the platform, or None if it has never refused us.

    Its `until` may be in the past: the row is kept after the wait ends,
    because how many refusals in a row it took is what the next one escalates
    from.
    """
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(PlatformRefusal.until, PlatformRefusal.consecutive_refusals)
            .where(PlatformRefusal.platform == platform)
        )).one_or_none()
    return Cooldown(until=row[0], consecutive=row[1]) if row is not None else None


async def refuse(
    platform: str, reason: str, *, base: timedelta, ceiling: timedelta
) -> Cooldown:
    """
    Record that the platform has refused us, and return the cool-down it now
    has: never shorter than one already recorded, and never fewer refusals in
    a row, since two fetches refused at the same moment each record one.

    Reading the row before writing it means two such fetches can compute the
    same escalation and record one refusal between them, as ADR 0009 already
    accepted for the request itself. Either way the wait only grows.
    """
    wanted = escalate(await current(platform), now=now_utc(), base=base, ceiling=ceiling)
    async with AsyncSessionLocal() as db:
        stmt = insert(PlatformRefusal).values(
            platform=platform,
            until=wanted.until,
            reason=reason,
            consecutive_refusals=wanted.consecutive,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PlatformRefusal.platform],
            set_={
                "until": func.greatest(PlatformRefusal.until, stmt.excluded.until),
                "reason": stmt.excluded.reason,
                "refused_at": func.now(),
                "consecutive_refusals": func.greatest(
                    PlatformRefusal.consecutive_refusals, stmt.excluded.consecutive_refusals
                ),
            },
        ).returning(PlatformRefusal.until, PlatformRefusal.consecutive_refusals)
        recorded = (await db.execute(stmt)).one()
        await db.commit()
    return Cooldown(until=recorded[0], consecutive=recorded[1])


async def let_through(platform: str) -> None:
    """
    The platform answered a request: whatever it refused before is over, so the
    next refusal starts again at the base wait (#140).

    The row stays, with when it last refused us and why, for whoever looks. Of
    a success and a refusal racing here, the success can be the later write and
    cost the block one doubling; it cannot shorten the wait in force, which is
    `until` and is not touched.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(PlatformRefusal)
            .where(PlatformRefusal.platform == platform, PlatformRefusal.consecutive_refusals > 0)
            .values(consecutive_refusals=0)
        )
        await db.commit()
