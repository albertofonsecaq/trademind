"""
Tests for #27 — a failed message threw away the record of what it had spent.

process_message runs inside one savepoint per message (#2), and the relevance
check runs near the start of it: call the provider, log the usage_event, then
distil and embed. So a distillation failure rolled the savepoint back and took
the relevance call's usage_event with it. The call was made, the tokens were
spent, the provider bills for them, and nothing recorded it.

That matters beyond tidiness: the spec derives the budget cap, the overage
metering and the margin protection from these rows, so an unlogged call is spend
that never reaches the cap. #9's backfill made 1,610 failing calls in one run.

A usage_event is a record that a call happened, which is true whatever becomes
of the work it was for — so it is written in its own transaction and nothing in
the caller's can undo it.
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.connectors.base import RawMessage
from app.services import ingestion_pipeline, usage_service


class _Savepoint:
    """The per-message savepoint, which must not be able to undo a usage row."""

    def __init__(self, recorder):
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._recorder.append("rollback" if exc_type else "release")
        return False


class _Session:
    """The caller's session: records what it was asked to write."""

    def __init__(self, duplicate=False):
        self.added: list = []
        self.savepoints: list[str] = []
        self.commits = 0
        self._duplicate = duplicate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        found = object() if self._duplicate else None
        return SimpleNamespace(
            scalar_one_or_none=lambda: found, scalar=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        return _Savepoint(self.savepoints)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def usage_log(monkeypatch):
    """Captures what the usage service writes, and how it writes it."""
    written = _Session()
    monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: written)
    return written


def _msg(**over) -> RawMessage:
    fields = {
        "stable_id": "telegram:chan:1",
        "source_config_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "text": "EURUSD looks like a breakout",
        "author": "someone",
        "channel": "chan",
        "timestamp": None,
        "content_type": "text",
    }
    return RawMessage(**{**fields, **over})


# ── the write does not belong to the caller's transaction ─────────────────────

class TestUsageIsWrittenOnItsOwn:
    def test_refuses_to_be_handed_a_session(self):
        """
        The contract, not just the behaviour: a caller cannot enlist this in a
        transaction that might undo it, because there is nowhere to pass one.
        """
        import inspect

        params = inspect.signature(usage_service.log_usage_event).parameters
        assert "db" not in params and "session" not in params
        assert all(p.kind is p.KEYWORD_ONLY for p in params.values())

        with pytest.raises(TypeError):
            usage_service.log_usage_event(
                _Session(),                       # a positional session
                workspace_id=uuid.uuid4(),
                task_type="relevance_filter",
                provider_model="claude-haiku-4-5-20251001",
            )

    async def test_commits_through_its_own_session(self, usage_log):
        await usage_service.log_usage_event(
            workspace_id=uuid.uuid4(),
            task_type="relevance_filter",
            provider_model="claude-haiku-4-5-20251001",
            input_units=120,
            output_units=8,
            cost_usd=Decimal("0.00004"),
        )

        assert len(usage_log.added) == 1
        assert usage_log.commits == 1        # committed, not left pending

    async def test_a_write_that_fails_does_not_take_the_work_with_it(self, monkeypatch):
        """
        If this write cannot happen the call goes unrecorded either way, so
        failing the caller would only add losing the work — and in a run of many
        messages it would fail every remaining one in turn (#9's shape).
        """
        class _Broken:
            async def __aenter__(self):
                raise RuntimeError("pool timeout")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: _Broken())

        await usage_service.log_usage_event(        # must not raise
            workspace_id=uuid.uuid4(),
            task_type="relevance_filter",
            provider_model="claude-haiku-4-5-20251001",
        )

    async def test_survives_a_savepoint_rollback_around_it(self, usage_log):
        """The shape of the bug, in miniature."""
        caller = _Session()

        with pytest.raises(RuntimeError):
            async with caller.begin_nested():
                await usage_service.log_usage_event(
                    workspace_id=uuid.uuid4(),
                    task_type="relevance_filter",
                    provider_model="claude-haiku-4-5-20251001",
                    cost_usd=Decimal("0.00004"),
                )
                raise RuntimeError("distillation failed")

        assert caller.savepoints == ["rollback"]
        assert len(usage_log.added) == 1      # the spend is still on the record


# ── the real path: relevance paid for, distillation fails ─────────────────────

class TestAFailedMessageStillRecordsWhatItSpent:
    @pytest.fixture
    def failing_distillation(self, monkeypatch):
        relevance_usage = {"input_tokens": 120, "output_tokens": 8}

        async def _on_topic(text, topic_scope):
            return True, "about a currency pair", relevance_usage

        async def _distill(*a, **kw):
            raise RuntimeError("distillation provider failed")

        monkeypatch.setattr(
            ingestion_pipeline.relevance_service, "is_on_topic", _on_topic
        )
        monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
        monkeypatch.setattr(ingestion_pipeline, "_detect_lang", lambda text: "en")

    async def test_the_relevance_call_is_still_logged(self, usage_log, failing_distillation):
        caller = _Session()

        with pytest.raises(RuntimeError):
            await ingestion_pipeline.process_message(caller, _msg(), "forex and indices")

        task_types = [e.task_type for e in usage_log.added]
        assert task_types == ["relevance_filter"]

    async def test_the_logged_cost_is_the_one_that_was_incurred(self, usage_log, failing_distillation):
        caller = _Session()

        with pytest.raises(RuntimeError):
            await ingestion_pipeline.process_message(caller, _msg(), "forex and indices")

        event = usage_log.added[0]
        assert event.input_units == 120
        assert event.output_units == 8
        assert event.cost_usd > 0

    async def test_pre_pipeline_cost_is_logged_before_anything_can_fail(self, usage_log, failing_distillation):
        """
        Whisper already ran in the connector, so its cost is owed whatever the
        rest of the message does.
        """
        caller = _Session()
        msg = _msg(
            pre_pipeline_cost_usd="0.006",
            pre_pipeline_task_type="transcription",
            pre_pipeline_model="whisper-1",
        )

        with pytest.raises(RuntimeError):
            await ingestion_pipeline.process_message(caller, msg, "forex and indices")

        assert [e.task_type for e in usage_log.added] == ["transcription", "relevance_filter"]


class TestEveryPaidCallIsRecordedBeforeAnythingCanSkipIt:
    """
    Each of these was paid for upstream of the thing that can go wrong, so the
    order of the logging call against that thing is the whole fix.
    """

    async def test_a_duplicate_still_records_the_transcription_it_paid_for(self, usage_log):
        """
        The connector transcribes to produce the message, so by the time the
        pipeline notices it already has this content, Whisper has been billed.
        """
        caller = _Session(duplicate=True)
        msg = _msg(
            pre_pipeline_cost_usd="0.006",
            pre_pipeline_task_type="transcription",
            pre_pipeline_model="whisper-1",
        )

        item = await ingestion_pipeline.process_message(caller, msg, "forex")

        assert item is None                                  # skipped as duplicate
        assert [e.task_type for e in usage_log.added] == ["transcription"]
        assert usage_log.added[0].cost_usd == Decimal("0.006")

    async def test_the_vision_call_is_recorded_before_the_row_is_flushed(self, usage_log, monkeypatch):
        """
        The flush raises on a duplicate stable_id, and the vision call that
        produced the content has already been paid for by then.
        """
        from sqlalchemy.exc import IntegrityError

        async def _extract(image_bytes, mime=None):
            return (
                {"is_on_topic": True, "description": "a EURUSD chart", "confidence": 0.9},
                {"input_tokens": 900, "output_tokens": 200},
            )

        monkeypatch.setattr(ingestion_pipeline.vision_service, "extract_image", _extract)

        class _FlushFails(_Session):
            async def flush(self):
                raise IntegrityError("duplicate stable_id", None, Exception())

        caller = _FlushFails()
        msg = _msg(content_type="image", text=None)
        msg.metadata = {"image_base64": "Zm9v"}

        with pytest.raises(IntegrityError):
            await ingestion_pipeline.process_message(caller, msg, "forex")

        assert [e.task_type for e in usage_log.added] == ["vision"]
        assert usage_log.added[0].input_units == 900

    async def test_each_video_frame_is_recorded_as_it_returns(self, usage_log, monkeypatch, tmp_path):
        """
        One event per video lost every earlier frame's cost when a later frame
        failed — and the earlier frames were already paid for.
        """
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"not really a video")

        async def _frames(path):
            return [b"frame1", b"frame2", b"frame3"]

        calls = {"n": 0}

        async def _extract(frame_bytes, mime=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("vision provider failed")
            return (
                {"is_on_topic": True, "description": f"frame {calls['n']}"},
                {"input_tokens": 500, "output_tokens": 100},
            )

        monkeypatch.setattr(ingestion_pipeline.keyframe_service, "extract_keyframes", _frames)
        monkeypatch.setattr(ingestion_pipeline.vision_service, "extract_image", _extract)

        caller = _Session()
        msg = _msg(content_type="video", text=None)
        msg.metadata = {"video_path": str(video)}

        with pytest.raises(RuntimeError):
            await ingestion_pipeline.process_message(caller, msg, "forex")

        # the two frames that succeeded are both on the record
        assert [e.task_type for e in usage_log.added] == ["vision", "vision"]
        assert sum(e.input_units for e in usage_log.added) == 1000
