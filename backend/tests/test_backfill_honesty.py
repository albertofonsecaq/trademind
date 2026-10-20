"""
Tests for #9 — a backfill said "completed" after dropping 1,610 messages.

A provider usage limit failed every message from mid-range onward. Each failure
rolled back its own savepoint and was logged, and then the job recorded
status=completed, items_ingested=1043, error_message=NULL. Nothing in the row
distinguished a source that genuinely had 1,043 new messages from one that had
2,653 and lost most of them, and the only trace was in a log that rotates.

Two things are wrong in that story and both are tested here: failures are not
counted, and a failure that will repeat for every remaining message is treated
as if it might not.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.api import sources as sources_api
from app.connectors.base import RawMessage
from app.core.llm_errors import is_fatal_for_run
from app.models.backfill_job import BackfillJob
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace
from app.services import ingestion_pipeline
from tests.conftest import FakeSession

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

USAGE_LIMIT = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-10-01."
)


def _status_error(cls, status, message):
    response = httpx.Response(status, request=REQUEST, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


# ── which failures doom the rest of the run ───────────────────────────────────

class TestFatalProviderErrors:
    def test_a_usage_limit_is_fatal(self):
        """The #9 case: every later message will hit the same wall."""
        assert is_fatal_for_run(
            _status_error(anthropic.BadRequestError, 400, USAGE_LIMIT)
        )

    def test_bad_credentials_are_fatal(self):
        assert is_fatal_for_run(
            _status_error(anthropic.AuthenticationError, 401, "API key is invalid.")
        )
        assert is_fatal_for_run(
            _status_error(anthropic.PermissionDeniedError, 403, "forbidden")
        )

    def test_a_rate_limit_is_not_fatal(self):
        """Transient by definition — the answer is backoff, not abandoning the range."""
        assert not is_fatal_for_run(
            _status_error(anthropic.RateLimitError, 429, "slow down")
        )

    def test_an_ordinary_bad_request_is_not_fatal(self):
        """One oversized image must not condemn the other 1,609 messages."""
        assert not is_fatal_for_run(
            _status_error(anthropic.BadRequestError, 400, "image exceeds 5 MB")
        )

    def test_ingested_content_cannot_trigger_a_false_positive(self):
        """
        A trading message discussing a credit balance is echoed back inside the
        serialized exception. Matching on that would abandon the whole range
        over somebody else's wording.
        """
        echoed = _status_error(
            anthropic.BadRequestError, 400, "messages.0.content: invalid image"
        )
        echoed.body = {
            "error": {
                "message": "messages.0.content: invalid image",
                "echo": "the trader wrote: watch your credit balance and usage limit",
            }
        }
        assert not is_fatal_for_run(echoed)

    def test_a_plain_error_is_not_fatal(self):
        assert not is_fatal_for_run(RuntimeError("something local broke"))


# ── fakes ─────────────────────────────────────────────────────────────────────

def _job(**over) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
        status="queued", items_ingested=0, items_failed=0,
        date_range_start=datetime(2026, 1, 22), date_range_end=None,
        completed_at=None, error_message=None, **over,
    )


def _source() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), identifier="chan", source_type="telegram",
        content_filters={"text": True}, is_paused=False, paused_at=None,
        last_fetched_id=None, last_fetched_item_at=None, last_fetched_at=None,
        connection=SimpleNamespace(status="active"),
    )


def _msg(n: int) -> RawMessage:
    return RawMessage(
        stable_id=f"telegram:chan:{n}",
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        text=f"message {n}",
        author="someone",
        channel="chan",
        timestamp=datetime(2026, 2, n if 1 <= n <= 28 else 1),
        content_type="text",
    )


@pytest.fixture
def backfill(monkeypatch):
    """_run_backfill_bg wired to a stub connector, returning the job it wrote."""

    def run(process, *, count=5):
        job, source = _job(), _source()
        workspace = SimpleNamespace(topic_scope="trading", ingestion_paused=False)
        attempted: list[str] = []
        messages = [_msg(n) for n in range(1, count + 1)]

        class _Connector:
            async def fetch_range(self, start, end):
                for m in messages:
                    attempted.append(m.stable_id)
                    yield m

        monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda s: _Connector())
        monkeypatch.setattr(ingestion_pipeline, "process_message", process)
        db = FakeSession(
            rows={BackfillJob: job, SourceConfig: source, Workspace: workspace}
        )
        monkeypatch.setattr(sources_api, "AsyncSessionLocal", lambda: db)

        async def go():
            await sources_api._run_backfill_bg(job_id=job.id)
            return SimpleNamespace(job=job, attempted=attempted, db=db)

        return go()

    return run


async def _ok(db, msg, topic_scope):
    return SimpleNamespace(id=uuid.uuid4())


def _fails_from(nth: int, exc):
    async def process(db, msg, topic_scope):
        if int(msg.stable_id.rsplit(":", 1)[1]) >= nth:
            raise exc
        return SimpleNamespace(id=uuid.uuid4())
    return process


# ── the job row has to describe what happened ─────────────────────────────────

class TestBackfillCountsFailures:
    async def test_a_clean_run_is_completed(self, backfill):
        result = await backfill(_ok, count=3)

        assert result.job.status == "completed"
        assert result.job.items_ingested == 3
        assert result.job.items_failed == 0
        assert result.job.error_message is None

    async def test_a_run_that_lost_messages_is_not_completed(self, backfill):
        """The bug in one assertion: this used to read 'completed'."""
        result = await backfill(_fails_from(3, RuntimeError("relevance check blew up")), count=5)

        assert result.job.status == "partial"

    async def test_counts_both_sides(self, backfill):
        result = await backfill(_fails_from(3, RuntimeError("relevance check blew up")), count=5)

        assert result.job.items_ingested == 2
        assert result.job.items_failed == 3

    async def test_the_row_says_why(self, backfill):
        """Without this the only trace was a log line that rotates."""
        result = await backfill(_fails_from(3, RuntimeError("relevance check blew up")), count=5)

        assert "relevance check blew up" in result.job.error_message


# ── a doomed run stops instead of grinding through the range ──────────────────

class TestBackfillStopsOnAFatalError:
    @pytest.fixture
    def fatal(self):
        return _status_error(anthropic.BadRequestError, 400, USAGE_LIMIT)

    async def test_marks_the_job_failed(self, backfill, fatal):
        result = await backfill(_fails_from(2, fatal), count=5)

        assert result.job.status == "failed"

    async def test_stops_at_the_first_one(self, backfill, fatal):
        """1,610 doomed provider calls took real time and taught us nothing."""
        result = await backfill(_fails_from(2, fatal), count=5)

        assert result.attempted == ["telegram:chan:1", "telegram:chan:2"]

    async def test_keeps_what_it_had_already_ingested(self, backfill, fatal):
        result = await backfill(_fails_from(2, fatal), count=5)

        assert result.job.items_ingested == 1
        # Summarized, not quoted: see TestWhatTheAbortedRowSays
        assert "limit or credit is exhausted" in result.job.error_message


# ── the poller had the same blind spot ────────────────────────────────────────

class TestFetchPipelineReportsFailures:
    @pytest.fixture
    def pipeline(self, monkeypatch):
        def run(process, *, count=5):
            source, attempted = _source(), []
            messages = [_msg(n) for n in range(1, count + 1)]

            class _Connector:
                async def fetch_new(self, position):
                    for m in messages:
                        attempted.append(m.stable_id)
                        yield m

                def repaired_position(self):
                    return None

            monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda s: _Connector())
            monkeypatch.setattr(ingestion_pipeline, "process_message", process)
            return source, attempted, FakeSession(rows={SourceConfig: source})

        return run

    async def test_reports_ingested_and_failed(self, pipeline):
        source, _, db = pipeline(_fails_from(3, RuntimeError("boom")), count=5)

        outcome = await ingestion_pipeline.run_fetch_pipeline(
            db, source=source, workspace=SimpleNamespace(topic_scope="trading")
        )

        assert (outcome.ingested, outcome.failed) == (2, 3)

    async def test_a_fatal_error_stops_the_source_and_surfaces(self, pipeline):
        """
        The poller catches per-source, so raising is how it gets reported — and
        how the remaining messages are spared a call that cannot succeed.
        """
        fatal = _status_error(anthropic.AuthenticationError, 401, "API key is invalid.")
        source, attempted, db = pipeline(_fails_from(2, fatal), count=5)

        with pytest.raises(anthropic.AuthenticationError):
            await ingestion_pipeline.run_fetch_pipeline(
                db, source=source, workspace=SimpleNamespace(topic_scope="trading")
            )

        assert attempted == ["telegram:chan:1", "telegram:chan:2"]


class TestWhatTheAbortedRowSays:
    """
    The row has to name the failure that ended the run and admit how much of the
    range it never looked at — otherwise "failed, 1 failed" reads as one bad
    message rather than a half-covered range.
    """

    @pytest.fixture
    def fatal(self):
        return _status_error(anthropic.BadRequestError, 400, USAGE_LIMIT)

    async def test_names_the_failure_that_stopped_it_not_the_first_one(self, backfill, fatal):
        async def process(db, msg, topic_scope):
            n = int(msg.stable_id.rsplit(":", 1)[1])
            if n == 1:
                raise RuntimeError("image exceeds 5 MB")       # unrelated, non-fatal
            if n >= 3:
                raise fatal
            return SimpleNamespace(id=uuid.uuid4())

        result = await backfill(process, count=5)

        assert result.job.status == "failed"
        assert "image exceeds 5 MB" not in (result.job.error_message or "")
        assert "limit or credit is exhausted" in result.job.error_message

    async def test_says_where_it_stopped(self, backfill, fatal):
        result = await backfill(_fails_from(2, fatal), count=5)

        assert "2026-02-02" in result.job.error_message
        assert "not attempted" in result.job.error_message

    async def test_never_stores_the_provider_wording(self, backfill, fatal):
        """
        llm_errors refuses to pass a provider's message to a caller; a row that
        persists and displays it would be the same leak by another route.
        """
        result = await backfill(_fails_from(2, fatal), count=5)

        assert "regain access" not in (result.job.error_message or "")
        assert "2026-10-01" not in (result.job.error_message or "")

    async def test_each_failure_rolls_back_only_its_own_savepoint(self, backfill, fatal):
        result = await backfill(_fails_from(3, RuntimeError("boom")), count=5)

        assert result.db.savepoints.count("rollback") == 3
        assert result.db.rollbacks == 0          # never the shared session (#2)
