"""
How often YouTube is asked for a transcript, in one place (#151).

A backfill over months of a channel's videos asked for one transcript after
another as fast as YouTube answered: the burst a platform refuses. Every
request for a transcript — captions, or the audio Whisper needs — now waits
its turn here first, a minimum gap plus a little jitter after the one before
it. The pace belongs to the process, not to a source or a run, so every poll,
backfill and failed-message retry shares it.

A request a refusal's cool-down holds up is never made, so it never waits the
gap either, and does not count as a request the next one is spaced from. The
turn asks the cool-down (ADR 0009) before waiting, and again once the gap has
passed, since a refusal may have been recorded meanwhile — by the request just
ahead of it, most often. Asking is the turn's, not each caller's, so no request
to YouTube can be made without it.
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from app.core.config import settings
from app.core.platform_refusal import Cooldown
from app.services import youtube_cooldown

T = TypeVar("T")


class Pace:
    """Turns at least the gap apart, one at a time, on the clock it is given."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._lock = asyncio.Lock()
        self._last: float | None = None

    @asynccontextmanager
    async def turn(self, ready: Callable[[], Awaitable[T]]) -> AsyncIterator[T]:
        """
        A request's turn: entered once the gap since the last request has
        passed, and held while the request is made, so the next one is spaced
        from its end and sees how it went — a refusal it met included.

        `ready` says whether the request may be made, raising when it may not;
        what it returns is what the turn gives. It is asked before the gap is
        waited, and again after when there was one to wait, and a turn it
        raises in neither waits for nothing nor counts as a request.
        """
        async with self._lock:
            given = await ready()
            if self._last is not None:
                gap = settings.YOUTUBE_REQUEST_GAP_SECONDS + self._jitter(
                    0.0, settings.YOUTUBE_REQUEST_JITTER_SECONDS
                )
                wait = self._last + gap - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                    given = await ready()
            try:
                yield given
            finally:
                self._last = self._clock()


_pace = Pace()


def turn() -> AbstractAsyncContextManager[Cooldown | None]:
    """
    `async with` it around a transcript request to YouTube: YouTubeRefused,
    without waiting, while a refusal's cool-down is in force; otherwise the
    recorded cool-down, which has passed, or None.
    """
    return _pace.turn(youtube_cooldown.not_refused)
