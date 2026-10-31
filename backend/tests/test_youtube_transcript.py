"""
Tests for #69 — one YouTube video's transcript, without a YouTube source.

Turning a video into transcript evidence used to exist only inside the YouTube
channel connector, so only a YouTube source could do it. A Telegram channel
that shares a video link (#70) needs the same thing for one video: captions
first, Whisper when there are none, chunked, the Whisper cost carried once, and
the same stable ids — so the two paths recognise each other's videos.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.config import settings
from app.services import transcription_service, youtube_transcript

_WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _segments(n: int, seconds_each: float = 60.0) -> list[dict]:
    return [
        {"text": f"segment {i} about EURUSD", "start": i * seconds_each, "duration": seconds_each}
        for i in range(n)
    ]


@pytest.fixture
def youtube(monkeypatch):
    """Captions and Whisper, answered locally; records whether Whisper was asked."""
    state = {"captions": None, "whisper": None, "whisper_calls": 0}

    async def _captions(video_id):
        return state["captions"]

    async def _whisper(video_url):
        state["whisper_calls"] += 1
        return state["whisper"]

    monkeypatch.setattr(youtube_transcript, "fetch_captions", _captions)
    monkeypatch.setattr(transcription_service, "transcribe_youtube_video", _whisper)
    monkeypatch.setattr(settings, "YOUTUBE_CHUNK_SECONDS", 300)
    return state


async def _messages(video_id="dQw4w9WgXcQ", **over):
    fields = {
        "source_config_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "author": "a trader",
        "channel": "@chan",
        "timestamp": _WHEN,
        "source_type": "telegram",
        "metadata": {"message_id": 42},
    }
    return await youtube_transcript.video_transcript_messages(video_id, **{**fields, **over})


class TestCaptionsComeFirst:
    async def test_a_short_video_is_one_message_with_the_full_stable_id(self, youtube):
        youtube["captions"] = _segments(3)

        [msg] = await _messages()

        assert msg.stable_id == "youtube:dQw4w9WgXcQ:full"
        assert msg.content_type == "video_transcript"
        assert msg.text == "segment 0 about EURUSD segment 1 about EURUSD segment 2 about EURUSD"
        assert msg.pre_pipeline_cost_usd == "0"
        assert msg.pre_pipeline_task_type is None
        assert youtube["whisper_calls"] == 0

    async def test_a_long_video_is_chunked_with_numbered_stable_ids(self, youtube):
        youtube["captions"] = _segments(12)       # 12 minutes, 5-minute chunks

        msgs = await _messages()

        assert [m.stable_id for m in msgs] == [
            "youtube:dQw4w9WgXcQ:chunk_0",
            "youtube:dQw4w9WgXcQ:chunk_1",
            "youtube:dQw4w9WgXcQ:chunk_2",
        ]
        assert [m.metadata["chunk_start_seconds"] for m in msgs] == [0.0, 300.0, 600.0]
        assert msgs[1].metadata["video_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=300"


class TestWhisperWhenThereAreNoCaptions:
    async def test_the_cost_is_carried_on_the_first_chunk_only(self, youtube):
        youtube["whisper"] = (_segments(12), Decimal("0.072"))

        msgs = await _messages()

        assert [m.pre_pipeline_cost_usd for m in msgs] == ["0.072", "0", "0"]
        assert msgs[0].pre_pipeline_task_type == "transcription"
        assert msgs[0].pre_pipeline_model == settings.WHISPER_MODEL
        assert [m.pre_pipeline_task_type for m in msgs[1:]] == [None, None]

    async def test_no_captions_and_no_whisper_is_nothing(self, youtube):
        assert await _messages() == []
        assert youtube["whisper_calls"] == 1

    async def test_youtube_refusing_captions_still_falls_back_to_whisper(self, youtube, monkeypatch):
        # As before #124, until #125 waits a refusal out instead.
        async def _refused(video_id):
            raise youtube_transcript.CaptionsRefused(video_id)

        monkeypatch.setattr(youtube_transcript, "fetch_captions", _refused)
        youtube["whisper"] = (_segments(1), Decimal("0.006"))

        msgs = await _messages()
        assert youtube["whisper_calls"] == 1
        assert [m.stable_id for m in msgs] == ["youtube:dQw4w9WgXcQ:full"]


class TestTheCallerSaysWhoseEvidenceItIs:
    async def test_source_attribution_and_metadata_come_from_the_caller(self, youtube):
        youtube["captions"] = _segments(1)
        source_id, workspace_id = uuid.uuid4(), uuid.uuid4()

        [msg] = await _messages(
            source_config_id=source_id,
            workspace_id=workspace_id,
            metadata={"message_id": 42, "url": "https://youtu.be/dQw4w9WgXcQ"},
        )

        assert (msg.source_config_id, msg.workspace_id) == (source_id, workspace_id)
        assert (msg.source_type, msg.channel, msg.author, msg.timestamp) == (
            "telegram", "@chan", "a trader", _WHEN,
        )
        assert msg.metadata["message_id"] == 42
        assert msg.metadata["url"] == "https://youtu.be/dQw4w9WgXcQ"
        assert msg.metadata["video_id"] == "dQw4w9WgXcQ"
        assert msg.metadata["chunk_index"] == 0


class TestWhatWhisperCostIsNeverLost:
    """
    The cost rode on chunk 0, and a chunk with no text is not yielded — so a
    video whose opening minutes Whisper heard as silence was paid for and never
    recorded. The cost rides on the first message there is instead, and with
    none at all it is recorded here, since nothing downstream will see it.
    """

    @pytest.fixture
    def usage_log(self, monkeypatch):
        from app.services import usage_service
        from tests.conftest import FakeSession

        written = FakeSession()
        monkeypatch.setattr(usage_service, "AsyncSessionLocal", lambda: written)
        return written

    async def test_a_silent_opening_does_not_take_the_cost_with_it(self, youtube, usage_log):
        silent = [{"text": "  ", "start": i * 60.0, "duration": 60.0} for i in range(5)]
        youtube["whisper"] = (silent + _segments(5)[:5], Decimal("0.06"))

        msgs = await _messages()

        assert [m.pre_pipeline_cost_usd for m in msgs] == ["0.06"]
        assert msgs[0].pre_pipeline_task_type == "transcription"
        assert usage_log.added == []           # carried, so logged downstream, once

    async def test_a_transcript_with_no_text_at_all_is_still_recorded(self, youtube, usage_log):
        youtube["whisper"] = ([{"text": "", "start": 0.0, "duration": 60.0}], Decimal("0.006"))
        workspace_id = uuid.uuid4()

        assert await _messages(workspace_id=workspace_id) == []

        [event] = usage_log.added
        assert event.task_type == "transcription"
        assert event.cost_usd == Decimal("0.006")
        assert event.workspace_id == workspace_id
