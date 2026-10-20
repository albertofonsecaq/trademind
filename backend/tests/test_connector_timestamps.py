"""
Regression tests for #4 — fetch_range crashed on tz-aware bounds.

Every timestamp column is timestamptz, so bounds read back from the DB arrive
aware. Comparing those against a connector's own timestamps raised TypeError and
made the backfill endpoint unusable.

#4 fixed it by normalizing both sides down to naive. #34 reversed the direction:
connectors hand out aware UTC like everything else (ADR 1), and #37 deleted
`to_naive_utc` once it turned out no platform we talk to insists on naive.

Either way the property these tests pin is the same, and it is the one that
matters: a connector accepts a bound whichever way it arrives, and comparisons
inside it never mix the two. The unit tests for the deleted helper went with it;
this is where the guarantee actually lived.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from telethon.tl.types import Message, PeerChannel

from app.connectors.telegram import TelegramConnector

UTC_NOON = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


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

        The helper being stubbed is `to_aware_utc` now: #34 moved connectors to
        hand out aware UTC (ADR 1), so normalizing the bounds *up* is what keeps
        the comparison from mixing the two. The guarantee is the same one.
        """
        import app.connectors.telegram as telegram_module

        monkeypatch.setattr(telegram_module, "to_aware_utc", lambda dt: dt)
        monkeypatch.setattr(connector, "_ts", lambda msg: msg.date.replace(tzinfo=None))

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            await _collect(
                connector,
                datetime(2026, 9, 17, tzinfo=timezone.utc),
                datetime(2026, 9, 20, tzinfo=timezone.utc),
            )
