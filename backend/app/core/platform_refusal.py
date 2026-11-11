"""
A platform refusing us for a while, which is waited out (#125, ADR 0007).

Not the message's fault, and not a reason to stop the run: what the platform
refused becomes a failed message that does not count an attempt and is not
retried until `until`, while everything else carries on.

How long it is waited out is decided here, as arithmetic over the refusals
before it (#140, ADR 0010), so it can be read and tested without a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.content_unavailable import ContentUnavailable


class PlatformRefused(ContentUnavailable):
    """The platform refused, and will not be asked again before `until`."""

    def __init__(self, message: str, *, until: datetime):
        super().__init__(message)
        self.until = until

    def counts_an_attempt(self) -> bool:
        """Never: the platform refused us, and the message was not at fault (ADR 0007)."""
        return False


@dataclass(frozen=True)
class Cooldown:
    """
    Until when a platform is left alone, and how many refusals in a row asked
    for that wait.

    `consecutive` is 0 once the platform has answered a request since: the next
    refusal then starts again at the base wait.
    """

    until: datetime
    consecutive: int


def cooldown_length(consecutive: int, *, base: timedelta, ceiling: timedelta) -> timedelta:
    """
    The wait the nth refusal in a row earns: the base doubled once per refusal
    before it, never past the ceiling.

    Doubling is not about asking more politely. At one caption request an hour
    we are not refused for volume, and waiting longer does not get the platform
    to stop any sooner. What it buys is a wait as long as the platform has
    actually been refusing us — so days of refusals cost a day's wait, instead
    of the same hour re-armed 17 times, which is what the log recorded between
    2026-11-04 and 2026-11-11.
    """
    length = base
    for _ in range(max(consecutive, 1) - 1):
        if length >= ceiling:
            break
        length = length * 2
    return min(length, ceiling)


def escalate(
    previous: Cooldown | None, *, now: datetime, base: timedelta, ceiling: timedelta
) -> Cooldown:
    """
    The cool-down a refusal met now earns, given the one recorded before it.

    A refusal arriving while we were only just let back in is the same run of
    refusals carrying on, and waits longer than the last one did. One arriving
    long after the last wait ended is a run of its own and starts at the base,
    as does the first after the platform answered us (`consecutive == 0`).

    "Only just" is the last wait's own length: the longer a platform has been
    refusing us, the longer a quiet spell has to be before we read it as over.
    Nothing is known about a spell in which nothing was asked — a paused
    source, a stopped process — so it is not counted as the platform refusing.
    """
    consecutive = 1
    if previous is not None and previous.consecutive > 0:
        remembered_until = previous.until + cooldown_length(
            previous.consecutive, base=base, ceiling=ceiling
        )
        if now < remembered_until:
            consecutive = previous.consecutive + 1
    return Cooldown(
        until=now + cooldown_length(consecutive, base=base, ceiling=ceiling),
        consecutive=consecutive,
    )
