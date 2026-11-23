"""
How often YouTube is asked for a transcript, in one place (#151).

A backfill over months of a channel's videos asked for one transcript after
another as fast as YouTube answered: the burst a platform refuses. Every
request for a transcript — captions, or the audio Whisper needs — now waits
its turn here first, a minimum gap plus a little jitter after the one before
it. The pace belongs to the process, not to a source or a run, so every poll,
backfill and failed-message retry shares it.

A request a refusal's cool-down holds up is never made, so it never waits its
turn either: callers check the cool-down first (ADR 0009), and again once their
turn has come, since the request before it may have been refused.
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from app.core.config import settings


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
    async def turn(self) -> AsyncIterator[None]:
        """
        A request's turn: entered once the gap since the last request has
        passed, and held while the request is made, so the next one is spaced
        from its end and sees how it went — a refusal it met included.
        """
        async with self._lock:
            if self._last is not None:
                gap = settings.YOUTUBE_REQUEST_GAP_SECONDS + self._jitter(
                    0.0, settings.YOUTUBE_REQUEST_JITTER_SECONDS
                )
                wait = self._last + gap - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                yield
            finally:
                self._last = self._clock()


_pace = Pace()


def turn() -> AbstractAsyncContextManager[None]:
    """`async with` it around a transcript request to YouTube."""
    return _pace.turn()
