"""
Regression tests for #2 — a failed message killed the whole poll tick.

run_fetch_pipeline called session.rollback() when a message failed. That expires
every object in the session, including the SourceConfig rows the poller is still
iterating, so the next `source.source_type` triggered a sync lazy-load and raised
MissingGreenlet. Sources after the failing one were never polled — two of three
live sources had ingested nothing for two months because of it.

The fix is a savepoint per message: undo the message, keep the session usable.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.services import ingestion_pipeline
from app.connectors.base import RawMessage


class _Savepoint:
    def __init__(self, recorder):
        self._recorder = recorder

    async def __aenter__(self):
        self._recorder.append("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._recorder.append("rollback" if exc_type else "release")
        return False        # never swallow: the caller's except clauses decide


class _FakeSession:
    """Records the transaction calls the pipeline makes."""

    def __init__(self):
        self.savepoints: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def begin_nested(self):
        return _Savepoint(self.savepoints)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, *a, **kw):
        return SimpleNamespace(scalar_one_or_none=lambda: None)


def _msg(n: int) -> RawMessage:
    return RawMessage(
        stable_id=f"telegram:chan:{n}",
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        text=f"message {n}",
        author="someone",
        channel="chan",
        timestamp=None,
        content_type="text",
    )


@pytest.fixture
def pipeline(monkeypatch):
    """run_fetch_pipeline wired to a fake connector and a recording session."""
    db = _FakeSession()
    messages = [_msg(1), _msg(2), _msg(3)]

    class _Connector:
        async def fetch_new(self, since_id=None):
            for m in messages:
                yield m

    monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda source: _Connector())

    source = SimpleNamespace(
        id=uuid.uuid4(), content_filters={"text": True}, last_fetched_id=None,
    )
    workspace = SimpleNamespace(topic_scope="trading")

    async def run(process):
        monkeypatch.setattr(ingestion_pipeline, "process_message", process)
        count = await ingestion_pipeline.run_fetch_pipeline(
            db, source=source, workspace=workspace
        )
        return count, db

    return run


async def _ok(db, msg, topic_scope):
    return SimpleNamespace(id=uuid.uuid4())


async def _fails_on_second(db, msg, topic_scope):
    if msg.stable_id.endswith(":2"):
        raise RuntimeError("relevance check failed (e.g. a provider 401)")
    return SimpleNamespace(id=uuid.uuid4())


class TestFailingMessage:
    async def test_never_rolls_back_the_shared_session(self, pipeline):
        """The bug in one assertion: a session rollback expires the caller's rows."""
        _, db = await pipeline(_fails_on_second)
        assert db.rollbacks == 0

    async def test_undoes_the_message_with_a_savepoint(self, pipeline):
        _, db = await pipeline(_fails_on_second)
        assert "rollback" in db.savepoints          # the savepoint absorbed it

    async def test_keeps_processing_later_messages(self, pipeline):
        """A poisoned message must not cost us the rest of the batch."""
        count, _ = await pipeline(_fails_on_second)
        assert count == 2                           # 1 and 3 survived


class TestHealthyRun:
    async def test_commits_each_message(self, pipeline):
        count, db = await pipeline(_ok)
        assert count == 3
        assert db.commits >= 3
        assert db.rollbacks == 0

    async def test_wraps_every_message_in_a_savepoint(self, pipeline):
        _, db = await pipeline(_ok)
        assert db.savepoints.count("enter") == 3
