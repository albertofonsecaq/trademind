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
from datetime import datetime, timezone

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.connectors.base import RawMessage
from app.models.failed_message import FailedMessage
from app.models.source_config import SourceConfig
from app.services import ingestion_pipeline
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

    async def _process(db, msg, topic_scope):
        if msg.stable_id in failing:
            raise failing[msg.stable_id]
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
