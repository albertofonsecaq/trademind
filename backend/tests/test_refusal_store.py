"""
Tests for #130 — a platform's refusal is recorded where every fetch sees it.

Since #125 the cool-down after YouTube refused lived in the running process: a
restart forgot it and asked again, and a manual fetch or a backfill running
beside the poller could each ask before it was set. It is now a row in the
database, read by every fetch before it asks, so these run against a real one.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.services import refusal_store

_TEN = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(db_session, monkeypatch):
    """The store, each call in a session of its own over the test's transaction."""
    @asynccontextmanager
    async def _session():
        yield db_session

    monkeypatch.setattr(refusal_store, "AsyncSessionLocal", _session)
    return refusal_store


@pytest.mark.db
class TestARefusal:
    async def test_nothing_recorded_is_no_refusal(self, store):
        assert await store.refused_until("youtube") is None

    async def test_recorded_is_read_back_by_the_next_session(self, store):
        # What a restart, or another fetch, does: ask again from nothing in memory.
        await store.refuse("youtube", _TEN, "HTTP 429")

        assert await store.refused_until("youtube") == _TEN

    async def test_a_later_one_moves_it_forward(self, store):
        await store.refuse("youtube", _TEN, "HTTP 429")

        assert await store.refuse("youtube", _TEN + timedelta(hours=1), "IpBlocked") == _TEN + timedelta(hours=1)
        assert await store.refused_until("youtube") == _TEN + timedelta(hours=1)

    async def test_an_earlier_one_never_moves_it_back(self, store):
        # Two fetches refused at once record two ends; the later holds.
        await store.refuse("youtube", _TEN, "HTTP 429")

        assert await store.refuse("youtube", _TEN - timedelta(minutes=5), "HTTP 429") == _TEN
        assert await store.refused_until("youtube") == _TEN

    async def test_is_one_platforms_only(self, store):
        await store.refuse("youtube", _TEN, "HTTP 429")

        assert await store.refused_until("telegram") is None
