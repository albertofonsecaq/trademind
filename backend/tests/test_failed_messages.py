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
from datetime import datetime, timedelta, timezone

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.connectors.base import RawMessage
from app.core.platform_refusal import PlatformRefused
from app.models.failed_message import FailedMessage
from app.models.source_config import SourceConfig
from app.services import failed_message_service, ingestion_pipeline
from tests.conftest import make_source, make_workspace

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


_UNTIL = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def _refused() -> PlatformRefused:
    return PlatformRefused("YouTube is refusing caption requests until 10:00 UTC", until=_UNTIL)


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

    async def test_a_refusal_while_retrying_leaves_the_rest_waiting_unasked(self, setup, clock):
        setup.channel.new = [1, 2]
        setup.failing["telegram:chan:1"] = RuntimeError("relevance check timed out")
        setup.failing["telegram:chan:2"] = RuntimeError("relevance check timed out")
        await setup.fetch()
        setup.channel.new = []
        setup.channel.unreachable[1] = _refused()

        await setup.fetch()

        assert setup.channel.fetched_again == ["telegram:chan:1"]
        rows = await setup.recorded()
        assert [(r.attempts, r.retry_after) for r in rows] == [(1, _UNTIL), (1, _UNTIL)]

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
