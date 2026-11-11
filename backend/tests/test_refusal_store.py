"""
Tests for #130 — a platform's refusal is recorded where every fetch sees it.

Since #125 the cool-down after YouTube refused lived in the running process: a
restart forgot it and asked again, and a manual fetch or a backfill running
beside the poller could each ask before it was set. It is now a row in the
database, read by every fetch before it asks, so these run against a real one.

Since #140 the row also carries how many refusals in a row earned the wait,
which is what the next refusal escalates from and what a successful request
clears. How long each wait is is arithmetic, tested offline in
`test_refusal_backoff.py`; what is here is that the row remembers it.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.core.platform_refusal import Cooldown
from app.services import refusal_store

_TEN = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)


@pytest.fixture
def store(db_session, monkeypatch):
    """The store, each call in a session of its own over the test's transaction."""
    @asynccontextmanager
    async def _session():
        yield db_session

    monkeypatch.setattr(refusal_store, "AsyncSessionLocal", _session)
    monkeypatch.setattr(refusal_store, "now_utc", lambda: _TEN - HOUR)
    return refusal_store


async def _refuse(store, platform="youtube", reason="HTTP 429"):
    return await store.refuse(platform, reason, base=HOUR, ceiling=DAY)


@pytest.mark.db
class TestARefusal:
    async def test_nothing_recorded_is_no_refusal(self, store):
        assert await store.current("youtube") is None

    async def test_recorded_is_read_back_by_the_next_session(self, store):
        # What a restart, or another fetch, does: ask again from nothing in memory.
        await _refuse(store)

        assert (await store.current("youtube")).until == _TEN

    async def test_the_first_of_a_block_waits_the_base(self, store):
        assert await _refuse(store) == Cooldown(until=_TEN, consecutive=1)

    async def test_the_next_one_waits_longer(self, store, monkeypatch):
        await _refuse(store)
        monkeypatch.setattr(store, "now_utc", lambda: _TEN)

        assert await _refuse(store, reason="IpBlocked") == Cooldown(
            until=_TEN + 2 * HOUR, consecutive=2
        )

    async def test_an_earlier_end_never_moves_it_back(self, store, monkeypatch):
        # Two fetches refused at once record two ends; the later holds.
        await _refuse(store)
        monkeypatch.setattr(store, "now_utc", lambda: _TEN - 4 * HOUR)

        assert (await _refuse(store)).until == _TEN
        assert (await store.current("youtube")).until == _TEN

    async def test_is_one_platforms_only(self, store):
        await _refuse(store)

        assert await store.current("telegram") is None


@pytest.mark.db
class TestTheRefusalsInARow:
    async def test_a_request_the_platform_answers_clears_them(self, store):
        await _refuse(store)

        await store.let_through("youtube")

        answered = await store.current("youtube")
        assert answered.consecutive == 0
        # The wait in force is untouched: a success says we are through now,
        # not that a recorded wait was wrong.
        assert answered.until == _TEN

    async def test_clearing_them_is_harmless_when_nothing_refused_us(self, store):
        await store.let_through("youtube")

        assert await store.current("youtube") is None

    async def test_a_run_starts_again_at_the_base_after_one(self, store, monkeypatch):
        await _refuse(store)
        monkeypatch.setattr(store, "now_utc", lambda: _TEN)
        await _refuse(store)
        await store.let_through("youtube")

        monkeypatch.setattr(store, "now_utc", lambda: _TEN + 3 * HOUR)
        assert await _refuse(store) == Cooldown(until=_TEN + 4 * HOUR, consecutive=1)

    async def test_a_run_long_over_is_forgotten_by_the_row_and_not_only_the_arithmetic(
        self, store, monkeypatch
    ):
        """
        The count written is the one `escalate` worked out, not the greater of
        it and what the row held.

        Kept at the greater, a refusal arriving long after the last wait ended
        waited the base — `escalate` says it starts a run of its own — while
        the row went on saying the old count, so the reason and the log said
        "N in a row" for a wait of an hour, and the next refusal doubled from
        N and landed near the ceiling.
        """
        await _refuse(store)
        monkeypatch.setattr(store, "now_utc", lambda: _TEN)
        await _refuse(store)  # two in a row, a two-hour wait ending at noon

        # Nothing asked for a day: whatever was refusing us is not read as
        # going on (ADR 0010), so this is the first refusal of a new run.
        monkeypatch.setattr(store, "now_utc", lambda: _TEN + DAY)
        recorded = await _refuse(store)

        assert recorded == Cooldown(until=_TEN + DAY + HOUR, consecutive=1)
        assert await store.current("youtube") == recorded
