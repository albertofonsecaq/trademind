"""
Tests for #96 — a message that fails is retried, not skipped (ADR 0005).

The fetch position moves past a message whose processing raised, and before
this nothing remembered it: twelve links and sixty-three videos were lost that
way on 25–26 Oct and had to be recovered by hand. Now the failure is recorded,
each fetch retries the source's failures first, and after three failed attempts
a message is given up — kept, and shown, for a person.

A failure that would repeat for everything left in the run is not the message's
fault: it is neither recorded nor counted.
"""
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.api import sources as sources_api
from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.platform_refusal import PlatformRefused
from app.services.youtube_transcript import ExtractorOutOfDate, NeverTranscribable, NoTranscript
from app.models.failed_message import FailedMessage
from app.models.source_config import SourceConfig
from app.services import failed_message_service, ingestion_pipeline
from tests.conftest import FakeSession, make_source, make_workspace

_AT = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


def _run_fatal() -> anthropic.AuthenticationError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request, json={"error": {"message": "API key is invalid."}})
    return anthropic.AuthenticationError("API key is invalid.", response=response, body=None)


class FakeChannel:
    """A source as the fetch sees it: new messages, and any one of them again by id."""

    def __init__(self, source):
        self._source = source
        self.new: list[int] = []
        self.gone: set[int] = set()
        self.unreachable: dict[int, Exception] = {}
        self.fetched_again: list[str] = []
        # A message that comes back in parts when fetched again: a video
        # refused captions, whose transcript is chunked (#125).
        self.parts: dict[int, list[str]] = {}

    def message(self, msg_id: int) -> RawMessage:
        return RawMessage(
            stable_id=f"telegram:chan:{msg_id}",
            source_config_id=self._source.id,
            workspace_id=self._source.workspace_id,
            text=f"message {msg_id}",
            author="someone",
            channel="chan",
            timestamp=_AT,
            source_type="telegram",
            content_type="text",
        )

    async def fetch_new(self, position):
        for msg_id in self.new:
            yield self.message(msg_id)

    async def fetch_one(self, stable_id: str) -> RawMessage | None:
        self.fetched_again.append(stable_id)
        msg_id = int(stable_id.split(":")[2])
        if msg_id in self.unreachable:
            raise self.unreachable[msg_id]
        return None if msg_id in self.gone else self.message(msg_id)

    async def fetch_again(self, stable_id: str) -> list[RawMessage] | None:
        msg = await self.fetch_one(stable_id)
        if msg is None:
            return None
        parts = self.parts.get(int(stable_id.split(":")[2]))
        return [replace(msg, stable_id=p) for p in parts] if parts else [msg]

    def repaired_position(self):
        return None


@pytest.fixture
async def setup(db_session, monkeypatch):
    """A source, its channel, and a pipeline that fails on whichever ids it is told to."""
    workspace = await make_workspace(db_session)
    source = await make_source(db_session, workspace)
    channel = FakeChannel(source)
    failing: dict[str, Exception] = {}
    processed: list[str] = []

    already_stored: set[str] = set()

    async def _process(db, msg, topic_scope):
        if msg.stable_id in failing:
            raise failing[msg.stable_id]
        if msg.stable_id in already_stored:
            return None                 # what process_message says for a duplicate
        processed.append(msg.stable_id)
        return object()

    monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda s: channel)
    monkeypatch.setattr(ingestion_pipeline, "process_message", _process)

    async def fetch():
        return await ingestion_pipeline.run_fetch_pipeline(db_session, source=source, workspace=workspace)

    async def recorded() -> list[FailedMessage]:
        rows = await db_session.execute(
            select(FailedMessage).where(FailedMessage.source_config_id == source.id).order_by(FailedMessage.stable_id)
        )
        return list(rows.scalars())

    class Setup:
        pass

    s = Setup()
    s.source, s.channel, s.failing, s.processed = source, channel, failing, processed
    s.already_stored = already_stored
    s.fetch, s.recorded = fetch, recorded
    return s


@pytest.mark.db
class TestAMessageThatFails:
    async def test_is_recorded_while_the_position_moves_past_it(self, setup, db_session):
        setup.channel.new = [1, 2, 3]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")

        outcome = await setup.fetch()

        [row] = await setup.recorded()
        assert row.stable_id == "telegram:chan:2"
        assert row.error == "relevance check timed out"
        assert row.attempts == 1
        assert row.given_up_at is None
        assert row.message_at == _AT
        assert (outcome.ingested, outcome.failed) == (2, 1)
        source = (await db_session.execute(select(SourceConfig).where(SourceConfig.id == setup.source.id))).scalar_one()
        assert source.last_fetched_id == "telegram:chan:3"

    async def test_that_is_the_last_of_the_fetch_still_moves_the_position_past_it(self, setup, db_session):
        # #126: when every video of a YouTube source failed, the position never
        # moved, and each poll fetched and failed the same videos again.
        setup.channel.new = [1, 2]
        setup.failing["telegram:chan:1"] = RuntimeError("no transcript")
        setup.failing["telegram:chan:2"] = RuntimeError("no transcript")

        await setup.fetch()

        source = (await db_session.execute(select(SourceConfig).where(SourceConfig.id == setup.source.id))).scalar_one()
        assert source.last_fetched_id == "telegram:chan:2"
        assert source.last_fetched_item_at == _AT

    async def test_is_retried_first_on_the_next_fetch_and_forgotten_once_it_works(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.failing.clear()
        setup.channel.new = [3]

        outcome = await setup.fetch()

        assert setup.channel.fetched_again == ["telegram:chan:2"]
        assert setup.processed == ["telegram:chan:2", "telegram:chan:3"]
        assert await setup.recorded() == []
        assert outcome.ingested == 2

    async def test_that_keeps_failing_is_given_up_after_three_attempts(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("still broken")
        await setup.fetch()
        setup.channel.new = []

        await setup.fetch()
        [row] = await setup.recorded()
        assert (row.attempts, row.given_up_at) == (2, None)

        await setup.fetch()
        [row] = await setup.recorded()
        assert row.attempts == 3
        assert row.given_up_at is not None
        assert row.error == "still broken"

    async def test_given_up_is_not_retried_again(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("still broken")
        for _ in range(3):
            await setup.fetch()
            setup.channel.new = []
        setup.channel.fetched_again.clear()

        await setup.fetch()

        assert setup.channel.fetched_again == []
        [row] = await setup.recorded()
        assert row.attempts == 3

    async def test_the_source_no_longer_has_is_given_up_at_once(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.channel.new = []
        setup.channel.gone.add(2)

        await setup.fetch()

        [row] = await setup.recorded()
        assert row.given_up_at is not None
        assert row.attempts == 1
        assert "no longer" in row.error


    async def test_that_cannot_be_fetched_again_counts_an_attempt_and_does_not_stall_the_source(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.channel.unreachable[2] = ConnectionError("Telegram did not answer")
        setup.channel.new = [3]

        outcome = await setup.fetch()

        assert setup.processed == ["telegram:chan:3"]
        [row] = await setup.recorded()
        assert row.attempts == 2
        assert "Telegram did not answer" in row.error
        assert (outcome.ingested, outcome.failed) == (1, 1)

    async def test_stored_meanwhile_is_forgotten_without_counting_as_ingested(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.failing.clear()
        setup.already_stored.add("telegram:chan:2")
        setup.channel.new = []

        outcome = await setup.fetch()

        assert await setup.recorded() == []
        assert outcome.ingested == 0


@pytest.mark.db
class TestAGivenUpMessageSentBack:
    """#102: sent back by a person, it is retried by the next fetch like any other."""

    async def _given_up(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        for _ in range(3):
            await setup.fetch()
            setup.channel.new = []
        [row] = await setup.recorded()
        assert row.given_up_at is not None
        return row

    async def test_is_retried_by_the_next_fetch(self, setup, db_session):
        row = await self._given_up(setup)
        await failed_message_service.send_back(db_session, row)
        setup.failing.clear()

        await setup.fetch()

        assert setup.processed == ["telegram:chan:2"]
        assert await setup.recorded() == []

    async def test_a_sources_given_up_messages_sent_back_together_are_all_retried(self, setup, db_session):
        """#141: the page sends a whole source's back after an outage, and the next fetch takes them."""
        setup.channel.new = [2, 3]
        for msg_id in (2, 3):
            setup.failing[f"telegram:chan:{msg_id}"] = RuntimeError("relevance check timed out")
        for _ in range(3):
            await setup.fetch()
            setup.channel.new = []
        assert all(row.given_up_at is not None for row in await setup.recorded())

        sent_back = await failed_message_service.send_back_given_up(db_session, setup.source.id)
        setup.failing.clear()
        await setup.fetch()

        assert sent_back == 2
        assert sorted(setup.processed) == ["telegram:chan:2", "telegram:chan:3"]
        assert await setup.recorded() == []

    async def test_that_fails_three_more_times_is_given_up_again_with_the_new_reason(self, setup, db_session):
        row = await self._given_up(setup)
        await failed_message_service.send_back(db_session, row)
        setup.failing["telegram:chan:2"] = RuntimeError("the page is gone")

        for _ in range(2):
            await setup.fetch()
            [row] = await setup.recorded()
            assert row.given_up_at is None
        await setup.fetch()

        [row] = await setup.recorded()
        assert row.given_up_at is not None
        assert row.attempts == 3
        assert row.error == "the page is gone"


@pytest.mark.db
class TestAFailureThatEndsTheRun:
    async def test_is_not_recorded_against_the_message(self, setup):
        setup.channel.new = [1, 2]
        setup.failing["telegram:chan:1"] = _run_fatal()

        with pytest.raises(anthropic.AuthenticationError):
            await setup.fetch()

        assert await setup.recorded() == []

    async def test_during_a_retry_does_not_count_an_attempt(self, setup):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.channel.new = []
        setup.failing["telegram:chan:2"] = _run_fatal()

        with pytest.raises(anthropic.AuthenticationError):
            await setup.fetch()

        [row] = await setup.recorded()
        assert row.attempts == 1
        assert row.error == "relevance check timed out"


class TestAFailureNoAttemptCouldFix:
    """
    #137: content no attempt could ever get is given up on its first failure,
    keeping its reason, instead of spending three attempts proving it.
    """

    def _video(self) -> RawMessage:
        return RawMessage(
            stable_id="youtube:A8XsDk4BhyU",
            source_config_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            text=None,
            author="A trader",
            channel="UCBjArPvYiR2Uu2U1XlK4pRw",
            timestamp=_AT,
            source_type="youtube",
            content_type="youtube_video",
        )

    async def _recorded(self, exc: BaseException) -> FailedMessage:
        db = FakeSession()
        return await failed_message_service.record_failure(db, self._video(), exc)

    async def test_is_given_up_on_its_first_failure_with_the_reason_it_gave(self):
        row = await self._recorded(NeverTranscribable("A8XsDk4BhyU", "it is a live event that has ended"))

        assert row.given_up_at is not None
        assert row.attempts == 1
        assert row.retry_after is None
        assert row.error == "Video A8XsDk4BhyU can never be transcribed: it is a live event that has ended"

    async def test_while_a_video_with_no_captions_yet_is_retried(self):
        row = await self._recorded(NoTranscript("A8XsDk4BhyU", "Whisper produced nothing"))

        assert row.given_up_at is None
        assert row.attempts == 1
        assert row.retry_after is not None

    async def test_and_a_refusal_is_never_given_up(self):
        # ADR 0009: the platform's doing, not the video's, whatever it said.
        row = await self._recorded(PlatformRefused("YouTube is refusing us", until=_AT))

        assert row.given_up_at is None
        assert row.attempts == 0

    async def test_one_already_recorded_is_given_up_without_its_attempts_left(self):
        db = FakeSession()
        row = FailedMessage(stable_id="youtube:A8XsDk4BhyU", attempts=1)

        await failed_message_service.count_attempt(
            db, row, NeverTranscribable("A8XsDk4BhyU", "YouTube offers no audio for it to download")
        )

        assert row.given_up_at is not None
        assert row.attempts == 2
        assert "no audio" in row.error


class TestAFailureOfOurOwnTooling:
    """
    #139: a stale yt-dlp is ours, not the video's. Its three attempts would be
    spent on our bug and the backlog given up, so it spends none of them and
    waits — and is not given up either, because an upgrade makes it work.
    """

    def _stale(self) -> ExtractorOutOfDate:
        return ExtractorOutOfDate(
            "sW1axKLV-t4", "our yt-dlp can no longer read YouTube's player. Refresh it with "
            "`pip install -U yt-dlp`"
        )

    async def _recorded(self, exc: BaseException) -> FailedMessage:
        db = FakeSession()
        return await failed_message_service.record_failure(
            db,
            RawMessage(
                stable_id="youtube:sW1axKLV-t4",
                source_config_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                text=None,
                author="A trader",
                channel="UCBjArPvYiR2Uu2U1XlK4pRw",
                timestamp=_AT,
                source_type="youtube",
                content_type="youtube_video",
            ),
            exc,
        )

    async def test_is_recorded_without_an_attempt_and_waits_with_the_command_to_fix_it(self):
        row = await self._recorded(self._stale())

        assert row.attempts == 0
        assert row.given_up_at is None
        assert row.retry_after is not None
        assert "pip install -U yt-dlp" in row.error

    async def test_spends_none_of_the_attempts_of_a_message_already_recorded(self):
        db = FakeSession()
        row = FailedMessage(stable_id="youtube:sW1axKLV-t4", attempts=2)

        await failed_message_service.count_attempt(db, row, self._stale())

        assert row.attempts == 2
        assert row.given_up_at is None
        assert row.retry_after is not None

    async def test_leaves_its_turn_in_the_queue_behind_the_messages_not_yet_tried(self):
        # #138: a failure that was attempted is stamped, so the rotation moves
        # on instead of asking the same video every tick.
        db = FakeSession()
        row = FailedMessage(stable_id="youtube:sW1axKLV-t4", attempts=0, last_failed_at=None)

        await failed_message_service.count_attempt(db, row, self._stale())

        assert row.last_failed_at is not None


_UNTIL = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def _refused() -> PlatformRefused:
    return PlatformRefused("YouTube is refusing caption requests until 10:00 UTC", until=_UNTIL)


def _refused_until(until: datetime) -> PlatformRefused:
    return PlatformRefused(f"YouTube is refusing caption requests until {until:%H:%M} UTC", until=until)


@pytest.fixture
def clock(monkeypatch):
    """The moment failed messages are retried at, which a refusal is waited out against."""
    now = {"at": _UNTIL - timedelta(minutes=30)}
    monkeypatch.setattr(failed_message_service, "now_utc", lambda: now["at"])
    return now


@pytest.mark.db
class TestAPlatformRefusingIsWaitedOut:
    """#125, ADR 0007: a refusal is the platform's, not the message's, and not the run's."""

    async def test_the_message_is_recorded_without_an_attempt_and_the_fetch_carries_on(self, setup, clock, db_session):
        setup.channel.new = [1, 2, 3]
        setup.failing["telegram:chan:2"] = _refused()

        outcome = await setup.fetch()

        [row] = await setup.recorded()
        assert row.stable_id == "telegram:chan:2"
        assert row.attempts == 0
        assert row.retry_after == _UNTIL
        assert row.error == "YouTube is refusing caption requests until 10:00 UTC"
        assert setup.processed == ["telegram:chan:1", "telegram:chan:3"]
        assert (outcome.ingested, outcome.failed) == (2, 1)
        source = (await db_session.execute(select(SourceConfig).where(SourceConfig.id == setup.source.id))).scalar_one()
        assert source.last_fetched_id == "telegram:chan:3"

    async def test_it_is_not_asked_for_again_before_the_refusal_ends(self, setup, clock):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []

        clock["at"] = _UNTIL - timedelta(seconds=1)
        await setup.fetch()

        assert setup.channel.fetched_again == []

    async def test_it_is_retried_once_the_refusal_has_ended(self, setup, clock):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []
        del setup.failing["telegram:chan:2"]

        clock["at"] = _UNTIL
        outcome = await setup.fetch()

        assert setup.channel.fetched_again == ["telegram:chan:2"]
        assert outcome.ingested == 1
        assert await setup.recorded() == []

    async def test_refused_again_and_again_it_is_never_given_up(self, setup, clock):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []

        for _ in range(4):
            clock["at"] += timedelta(hours=1)
            await setup.fetch()

        [row] = await setup.recorded()
        assert len(setup.channel.fetched_again) == 4
        assert (row.attempts, row.given_up_at) == (0, None)

    async def test_a_refusal_while_retrying_leaves_the_other_refused_waiting_unasked(self, setup, clock):
        setup.channel.new = [1, 2]
        setup.failing["telegram:chan:1"] = _refused()
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []
        setup.channel.unreachable[1] = _refused_until(_UNTIL + timedelta(hours=1))

        clock["at"] = _UNTIL
        await setup.fetch()

        assert setup.channel.fetched_again == ["telegram:chan:1"]
        rows = await setup.recorded()
        assert [(r.attempts, r.retry_after) for r in rows] == [(0, _UNTIL + timedelta(hours=1))] * 2

    async def test_each_cool_down_asks_for_a_different_message(self, setup, clock):
        """
        #138: the one request a cool-down allows goes to the message that has
        waited longest for its turn, so a block delays the backlog instead of
        spending every cool-down on the same message — which is what ordering
        the queue by first failure did for seven days.
        """
        setup.channel.new = [1, 2, 3]
        for n in (1, 2, 3):
            setup.failing[f"telegram:chan:{n}"] = _refused()
        await setup.fetch()
        setup.channel.new = []
        setup.failing.clear()
        setup.channel.fetched_again.clear()

        asked_per_cool_down = []
        # Ahead of when the rows were recorded, as a live source's clock is:
        # the block began hours before the cool-down that lifts now.
        at = now_utc() + timedelta(hours=1)
        for _ in range(3):
            clock["at"] = at
            refused_again = _refused_until(at + timedelta(hours=1))
            setup.channel.unreachable = {n: refused_again for n in (1, 2, 3)}
            await setup.fetch()
            asked_per_cool_down.append(list(setup.channel.fetched_again))
            setup.channel.fetched_again.clear()
            at += timedelta(hours=1)

        assert [len(asked) for asked in asked_per_cool_down] == [1, 1, 1]
        assert len({asked[0] for asked in asked_per_cool_down}) == 3

    async def test_a_refusal_while_retrying_does_not_hold_up_an_ordinary_failure(self, setup, clock):
        setup.channel.new = [1, 2]
        setup.failing["telegram:chan:1"] = _refused()
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.channel.new = []
        setup.channel.unreachable[1] = _refused_until(_UNTIL + timedelta(hours=1))
        del setup.failing["telegram:chan:2"]

        clock["at"] = _UNTIL
        outcome = await setup.fetch()

        assert setup.channel.fetched_again == ["telegram:chan:1", "telegram:chan:2"]
        assert outcome.ingested == 1
        [row] = await setup.recorded()
        assert row.stable_id == "telegram:chan:1"

    async def test_an_ordinary_failure_after_a_refusal_counts_and_is_retried_at_once(self, setup, clock):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")

        clock["at"] = _UNTIL
        await setup.fetch()

        [row] = await setup.recorded()
        assert (row.attempts, row.retry_after) == (1, None)


@pytest.mark.db
class TestAMessageThatComesBackInParts:
    """#125: a refused video fetched again is its transcript's chunks, each its own message."""

    async def test_a_part_that_fails_is_remembered_on_its_own_and_the_rest_is_stored(self, setup, clock):
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = _refused()
        await setup.fetch()
        setup.channel.new = []
        del setup.failing["telegram:chan:2"]
        setup.channel.parts[2] = ["telegram:chan:2:chunk_0", "telegram:chan:2:chunk_1", "telegram:chan:2:chunk_2"]
        setup.failing["telegram:chan:2:chunk_1"] = RuntimeError("relevance check timed out")

        clock["at"] = _UNTIL
        outcome = await setup.fetch()

        assert setup.processed == ["telegram:chan:2:chunk_0", "telegram:chan:2:chunk_2"]
        [row] = await setup.recorded()
        assert (row.stable_id, row.attempts, row.error) == ("telegram:chan:2:chunk_1", 1, "relevance check timed out")
        assert (outcome.ingested, outcome.failed) == (2, 1)


@pytest.mark.db
class TestAVideoWithNoTranscript:
    """#126: retried with growing gaps, so captions YouTube adds hours after upload are not missed."""

    async def test_is_retried_an_hour_later_then_a_day_later_then_given_up(self, setup, clock):
        start = clock["at"]
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = NoTranscript("v2", "Whisper produced nothing")
        await setup.fetch()
        setup.channel.new = []

        [row] = await setup.recorded()
        assert (row.attempts, row.retry_after) == (1, start + timedelta(hours=1))

        clock["at"] = start + timedelta(minutes=59)
        await setup.fetch()
        assert setup.channel.fetched_again == []

        clock["at"] = start + timedelta(hours=1)
        await setup.fetch()
        [row] = await setup.recorded()
        assert (row.attempts, row.retry_after) == (2, start + timedelta(hours=24))

        clock["at"] = start + timedelta(hours=24)
        await setup.fetch()
        [row] = await setup.recorded()
        assert row.attempts == 3
        assert row.given_up_at is not None
        assert len(setup.channel.fetched_again) == 2


@pytest.mark.db
class TestAVideoThatCanNeverBeTranscribed:
    """#137: given up on its first failure, so nothing asks the platform for it again."""

    async def test_is_given_up_at_once_and_not_retried_a_day_later(self, setup, clock):
        start = clock["at"]
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = NeverTranscribable("v2", "it is a live event that has ended")

        outcome = await setup.fetch()

        [row] = await setup.recorded()
        assert row.given_up_at is not None
        assert row.attempts == 1
        assert row.error == "Video v2 can never be transcribed: it is a live event that has ended"
        assert (outcome.ingested, outcome.failed) == (0, 1)

        setup.channel.new = []
        clock["at"] = start + timedelta(days=1)
        await setup.fetch()

        assert setup.channel.fetched_again == []


@pytest.mark.db
class TestAVideoOurOwnStaleToolingFailed:
    """
    #139: a yt-dlp YouTube has outgrown fails every video it is asked for. Read
    as each video's own failure it gave up the whole backlog in three fetches;
    counting nothing, the backlog is there to fetch once the tool is refreshed.
    """

    def _stale(self) -> ExtractorOutOfDate:
        return ExtractorOutOfDate(
            "v2", "our yt-dlp can no longer read YouTube's player. Refresh it with "
            "`pip install -U yt-dlp`"
        )

    async def test_waits_unattempted_and_an_ordinary_retry_ingests_it_after_the_refresh(self, setup, clock):
        start = clock["at"]
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = self._stale()
        await setup.fetch()
        setup.channel.new = []

        [row] = await setup.recorded()
        assert (row.attempts, row.retry_after) == (0, start + timedelta(hours=1))
        assert "pip install -U yt-dlp" in row.error

        # Not asked again meanwhile: a stale tool fails for every video of the
        # source, and one request each a minute is the flood ADR 0007 names.
        clock["at"] = start + timedelta(minutes=59)
        await setup.fetch()
        assert setup.channel.fetched_again == []

        # Three more hours. An ordinary failure would have been given up by now.
        for hours in (1, 2, 3):
            clock["at"] = start + timedelta(hours=hours)
            await setup.fetch()

        [row] = await setup.recorded()
        assert (row.attempts, row.given_up_at) == (0, None)
        assert len(setup.channel.fetched_again) == 3

        # The tool refreshed, and nothing sent back by hand.
        del setup.failing["telegram:chan:2"]
        clock["at"] = start + timedelta(hours=4)
        outcome = await setup.fetch()

        assert outcome.ingested == 1
        assert await setup.recorded() == []


@pytest.mark.db
class TestStoringAPartOfAMessage:
    async def test_forgets_the_failure_recorded_for_the_whole_of_it(self, setup, db_session):
        # A backfill stores a video's chunks; its failure was recorded as the video (#126).
        setup.channel.new = [2]
        setup.failing["telegram:chan:2"] = RuntimeError("no transcript")
        await setup.fetch()

        await failed_message_service.forget(db_session, setup.source.id, "telegram:chan:2:chunk_0")
        await db_session.commit()

        assert await setup.recorded() == []

    async def test_does_not_forget_a_message_whose_id_it_only_starts_like(self, setup, db_session):
        setup.channel.new = [4]
        setup.failing["telegram:chan:4"] = RuntimeError("no transcript")
        await setup.fetch()

        await failed_message_service.forget(db_session, setup.source.id, "telegram:chan:42:chunk_0")
        await db_session.commit()

        assert len(await setup.recorded()) == 1


# ── a stuck message (#141) ────────────────────────────────────────────────────
_STUCK_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _waiting(
    *,
    waiting_since: timedelta,
    until: timedelta,
    attempts: int = 0,
    given_up: bool = False,
    stable_id: str = "youtube:BlBoKnm9O_o",
) -> FailedMessage:
    """A row as a refusal leaves it: first failed a while ago, not retried before `until`."""
    first_failed_at = _STUCK_NOW - waiting_since
    return FailedMessage(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        source_config_id=uuid.uuid4(),
        stable_id=stable_id,
        error="YouTube is refusing caption requests",
        attempts=attempts,
        first_failed_at=first_failed_at,
        last_failed_at=first_failed_at,
        retry_after=_STUCK_NOW + until,
        given_up_at=_STUCK_NOW if given_up else None,
    )


class TestAStuckMessage:
    """
    #141: a message that waits out one refusal after another reads exactly like
    one that will go through in an hour, and nothing ever gives it up (ADR 0007).
    """

    @pytest.fixture(autouse=True)
    def _now(self, monkeypatch):
        monkeypatch.setattr(failed_message_service, "now_utc", lambda: _STUCK_NOW)

    def test_has_waited_longer_than_one_cool_down_could_last_with_no_attempt_counted(self):
        row = _waiting(waiting_since=timedelta(days=7), until=timedelta(hours=4))

        assert failed_message_service.is_stuck(row) is True

    def test_waiting_out_the_cool_down_it_first_failed_in_is_merely_waiting(self):
        # A day's cool-down, met the moment the message first failed: one block,
        # and the page already says when it will be retried (#132).
        row = _waiting(waiting_since=timedelta(minutes=1), until=timedelta(hours=23))

        assert failed_message_service.is_stuck(row) is False

    def test_is_never_one_an_attempt_has_been_counted_against(self):
        # Three attempts is what gives a message up; this one is spending them.
        row = _waiting(waiting_since=timedelta(days=7), until=timedelta(hours=4), attempts=1)

        assert failed_message_service.is_stuck(row) is False

    def test_is_not_one_whose_wait_has_already_ended(self):
        row = _waiting(waiting_since=timedelta(days=7), until=-timedelta(minutes=1))

        assert failed_message_service.is_stuck(row) is False

    def test_is_not_one_that_has_been_given_up(self):
        row = _waiting(waiting_since=timedelta(days=7), until=timedelta(hours=4), given_up=True)

        assert failed_message_service.is_stuck(row) is False

    def test_is_one_waiting_on_our_own_stale_tooling_just_as_much(self):
        # #139, ADR 0011: an out-of-date yt-dlp counts no attempt either, and
        # waits an hour at a time, so a day of it is many waits and no progress.
        row = _waiting(waiting_since=timedelta(days=2), until=timedelta(hours=1))
        row.error = "Video sW1axKLV-t4 is waiting for an up-to-date yt-dlp: ..."

        assert failed_message_service.is_stuck(row) is True

    def test_is_measured_against_the_longest_cool_down_the_ladder_allows(self):
        row = _waiting(waiting_since=timedelta(hours=3), until=timedelta(hours=1))

        assert failed_message_service.is_stuck(row, longest_cooldown=timedelta(days=1)) is False
        assert failed_message_service.is_stuck(row, longest_cooldown=timedelta(hours=2)) is True


class TestASourcesFailedMessageList:
    """
    #141: the page is told which messages are stuck, so the rule stays in the
    one place it is written instead of being guessed at again in the frontend.
    """

    async def test_says_of_each_waiting_message_whether_it_is_stuck(self, monkeypatch):
        monkeypatch.setattr(failed_message_service, "now_utc", lambda: _STUCK_NOW)
        source_id = uuid.uuid4()
        db = FakeSession(rows={
            SourceConfig: source_id,
            FailedMessage: [
                _waiting(waiting_since=timedelta(days=7), until=timedelta(hours=4), stable_id="youtube:stuck"),
                _waiting(waiting_since=timedelta(minutes=1), until=timedelta(hours=1), stable_id="youtube:waiting"),
            ],
        })

        listed = await sources_api.list_failed_messages(
            uuid.uuid4(), source_id, SimpleNamespace(id=uuid.uuid4()), db
        )

        assert {m.stable_id: m.stuck for m in listed} == {"youtube:stuck": True, "youtube:waiting": False}
