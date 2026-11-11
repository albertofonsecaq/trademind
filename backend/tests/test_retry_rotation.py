"""
Tests for #138 — under a block, each cool-down's one request goes to a
different video.

The retry queue was ordered oldest-first by first failure. A refusal gives
every waiting message the same cool-down end, so when it lifts they all become
eligible at once and the same oldest video is attempted first; its request
earns a fresh block for the whole platform, and everything behind it is refused
by the pre-check without ever being asked for. Measured on one channel source:
nine poll ticks between 2026-11-04 12:25 and 2026-11-05 08:37 produced 15
refusals across two video ids and ingested nothing, and `BlBoKnm9O_o` was still
taking the turn on 2026-11-11, seven days after it first failed.

So the turn goes to the least recently attempted message instead. A refusal
does not count an attempt (ADR 0007), so it needs no new column: when a message
was last attempted is already on its row, and a message nothing has been held
against yet goes first.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.platform_refusal import PlatformRefused
from app.models.failed_message import FailedMessage
from app.services import failed_message_service, ingestion_pipeline
from tests.conftest import FakeSession

_SOURCE = uuid.uuid4()
_WORKSPACE = uuid.uuid4()

# when the block began, and the cool-down end every waiting message was given
_T0 = datetime(2026, 11, 4, 12, 25, tzinfo=timezone.utc)
_UNTIL = _T0 + timedelta(minutes=60)


def _failed(
    stable_id: str,
    *,
    first: datetime,
    last: datetime | None = None,
    attempts: int = 0,
) -> FailedMessage:
    """A recorded failure, as a block leaves it: refused, so no attempt counted."""
    return FailedMessage(
        id=uuid.uuid4(),
        workspace_id=_WORKSPACE,
        source_config_id=_SOURCE,
        stable_id=stable_id,
        error="IpBlocked: YouTube is blocking requests from this address",
        attempts=attempts,
        first_failed_at=first,
        last_failed_at=last if last is not None else first,
        retry_after=_UNTIL,
    )


async def _turns(rows: list[FailedMessage]) -> list[str]:
    """Whose turn it is, in order, as the retry queue hands them out."""
    db = FakeSession(rows={FailedMessage: lambda _statement: list(rows)})
    return [row.stable_id for row in await failed_message_service.to_retry(db, _SOURCE)]


class TestWhoseTurnItIsNext:
    async def test_the_least_recently_attempted_message_goes_first(self):
        # The oldest failure is the one that has been attempted most recently:
        # it took the last cool-down's turn, so it takes this one's last.
        rows = [
            _failed("youtube:oldest", first=_T0, last=_T0 + timedelta(hours=3)),
            _failed("youtube:middle", first=_T0 + timedelta(hours=1)),
            _failed("youtube:newest", first=_T0 + timedelta(hours=2)),
        ]

        assert await _turns(rows) == ["youtube:middle", "youtube:newest", "youtube:oldest"]

    async def test_a_message_never_attempted_goes_before_ones_that_have_been(self):
        # A refusal does not count an attempt, so nothing is being held against
        # the refused message; the other has already failed on its own account.
        rows = [
            _failed("youtube:attempted", first=_T0, last=_T0 + timedelta(minutes=5), attempts=1),
            _failed("youtube:refused", first=_T0 + timedelta(hours=2)),
        ]

        assert await _turns(rows) == ["youtube:refused", "youtube:attempted"]

    async def test_messages_last_attempted_at_the_same_instant_keep_a_fixed_order(self):
        """
        One refusal stamps every waiting message with the same instant, so the
        order has to be settled by something unique to the message — otherwise
        two of them could swap places from one tick to the next and neither
        would ever reach the platform.
        """
        rows = [_failed(f"youtube:v{n}", first=_T0) for n in (3, 1, 2)]

        assert await _turns(rows) == ["youtube:v1", "youtube:v2", "youtube:v3"]

    async def test_the_same_state_produces_the_same_order_whatever_order_it_is_read_in(self):
        rows = [
            _failed("youtube:a", first=_T0, last=_T0 + timedelta(minutes=30)),
            _failed("youtube:b", first=_T0 + timedelta(hours=1), attempts=2),
            _failed("youtube:c", first=_T0 + timedelta(hours=2)),
        ]

        assert await _turns(rows) == await _turns(list(reversed(rows)))


@pytest.fixture
def blocked(monkeypatch):
    """
    A source with three refused videos and a platform that refuses whatever it
    is asked for: one tick of the fetch is one cool-down's single request.
    """
    rows = [
        _failed("youtube:v1", first=_T0),
        _failed("youtube:v2", first=_T0 + timedelta(minutes=1)),
        _failed("youtube:v3", first=_T0 + timedelta(minutes=2)),
    ]
    asked: list[str] = []

    class _RefusingYouTube:
        async def fetch_new(self, position):
            # The window holds nothing new; the backlog is all there is to do.
            return
            yield                                            # pragma: no cover

        async def fetch_again(self, stable_id: str):
            asked.append(stable_id)
            raise PlatformRefused(
                "IpBlocked: YouTube is blocking requests from this address",
                until=_UNTIL,
            )

        def repaired_position(self):
            return None

    monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda _source: _RefusingYouTube())

    source = SimpleNamespace(
        id=_SOURCE, content_filters={"video": True}, last_fetched_id=None,
        last_fetched_item_at=None, platform_connection_id=uuid.uuid4(),
    )
    workspace = SimpleNamespace(topic_scope="trading")
    # The cool-down has lifted, so every waiting message is eligible again —
    # which is the state the queue has to order.
    db = FakeSession(rows={FailedMessage: lambda _statement: list(rows)})

    async def tick():
        await ingestion_pipeline.run_fetch_pipeline(db, source=source, workspace=workspace)

    return SimpleNamespace(tick=tick, asked=asked, rows={row.stable_id: row for row in rows})


class TestThreeCoolDownsUnderASustainedBlock:
    async def test_attempt_three_different_videos(self, blocked):
        for _ in range(3):
            await blocked.tick()

        assert blocked.asked == ["youtube:v1", "youtube:v2", "youtube:v3"]

    async def test_the_video_that_took_the_turn_goes_to_the_back(self, blocked):
        await blocked.tick()

        last_attempted = blocked.rows["youtube:v1"].last_failed_at
        assert last_attempted > blocked.rows["youtube:v3"].last_failed_at

    async def test_a_video_not_asked_for_keeps_when_it_was_last_attempted(self, blocked):
        """
        It did not fail: nothing was asked of the platform on its behalf. Were
        the refusal stamped on it anyway, every message would move back together
        and the front of the queue would never change.
        """
        await blocked.tick()

        assert blocked.rows["youtube:v2"].last_failed_at == _T0 + timedelta(minutes=1)
        assert blocked.rows["youtube:v2"].retry_after == _UNTIL

    async def test_no_video_is_given_up_by_a_block_however_many_turns_it_waits(self, blocked):
        for _ in range(3):
            await blocked.tick()

        assert [row.attempts for row in blocked.rows.values()] == [0, 0, 0]
        assert all(row.given_up_at is None for row in blocked.rows.values())
