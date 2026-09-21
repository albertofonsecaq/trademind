"""
Regression tests for #4 — fetch_range crashed on tz-aware bounds.

Every timestamp column is timestamptz, so bounds read back from the DB arrive
aware, while connectors work in naive UTC. Comparing the two raised TypeError
and made the backfill endpoint unusable.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from telethon.tl.types import Message, PeerChannel

from app.connectors.base import to_naive_utc
from app.connectors.telegram import TelegramConnector

UTC_NOON = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


class TestToNaiveUtc:
    def test_strips_utc(self):
        assert to_naive_utc(UTC_NOON) == datetime(2026, 9, 18, 12)

    def test_converts_other_offsets_rather_than_stripping(self):
        # 14:00+02:00 is 12:00Z — a naive strip would wrongly yield 14:00.
        plus_two = datetime(2026, 9, 18, 14, tzinfo=timezone(timedelta(hours=2)))
        assert to_naive_utc(plus_two) == datetime(2026, 9, 18, 12)

    def test_passes_naive_through(self):
        naive = datetime(2026, 9, 18, 12)
        assert to_naive_utc(naive) is naive

    def test_passes_none_through(self):
        assert to_naive_utc(None) is None


def _message(msg_id: int, when: datetime) -> Message:
    msg = Message(id=msg_id, peer_id=PeerChannel(123), date=when, message=f"update {msg_id}")
    msg._text = msg.message      # a detached Message resolves .text lazily via a client
    return msg


class _FakeTelegramClient:
    """Stands in for TelegramClient: yields two messages, one day apart."""

    def __init__(self, dates):
        self._dates = dates

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def iter_messages(self, entity, reverse=True, offset_date=None):
        async def gen():
            for i, when in enumerate(self._dates, start=1):
                yield _message(i, when)
        return gen()


@pytest.fixture
def connector():
    c = TelegramConnector(
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        channel_identifier="123/456",
        api_id=1,
        api_hash="x",
        session_string="x",
        content_filters={"text": True},
    )
    dates = [UTC_NOON, UTC_NOON + timedelta(days=1)]
    c._resolve_identifier = lambda: "entity"
    c._client = lambda: _FakeTelegramClient(dates)
    return c


async def _collect(connector, start, end):
    return [msg.stable_id async for msg in connector.fetch_range(start, end)]


class TestFetchRangeBounds:
    """The bug: aware bounds (as read from timestamptz) blew up the comparison."""

    async def test_accepts_aware_bounds(self, connector):
        got = await _collect(
            connector,
            datetime(2026, 9, 17, tzinfo=timezone.utc),
            datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        assert len(got) == 2

    async def test_accepts_non_utc_aware_bounds(self, connector):
        got = await _collect(
            connector,
            datetime(2026, 9, 17, 2, tzinfo=timezone(timedelta(hours=2))),
            datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        assert len(got) == 2

    async def test_still_accepts_naive_bounds(self, connector):
        got = await _collect(connector, datetime(2026, 9, 17), datetime(2026, 9, 20))
        assert len(got) == 2

    async def test_catches_the_regression(self, connector, monkeypatch):
        """Restore the pre-fix behaviour and the crash must come back.

        Without this the suite could pass vacuously — the first draft of this
        test did exactly that, because stubbing the shared helper disabled both
        sides of the comparison at once.
        """
        import app.connectors.telegram as telegram_module

        monkeypatch.setattr(telegram_module, "to_naive_utc", lambda dt: dt)
        monkeypatch.setattr(connector, "_ts", lambda msg: msg.date.replace(tzinfo=None))

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            await _collect(
                connector,
                datetime(2026, 9, 17, tzinfo=timezone.utc),
                datetime(2026, 9, 20, tzinfo=timezone.utc),
            )
