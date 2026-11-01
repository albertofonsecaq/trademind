"""
Tests for #125 — a video YouTube refused captions for is handed on, not lost.

Before, the connector asked for the video's transcript, got nothing, and yielded
nothing: no failure was recorded and the fetch position never moved, so every
poll asked YouTube for the same 79 videos again, about 58,000 times over three
days. Now a refused video comes out of the connector as the video itself, whose
transcript the pipeline asks for, so the refusal is recorded against it and
waited out like any other (ADR 0007).
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.connectors.base import FetchPosition, RawMessage
from app.connectors.youtube import YouTubeConnector
from app.services import youtube_transcript
from tests.conftest import fake_youtube_window

_UNTIL = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)

# the channel window, newest first, as the API returns it
WINDOW = [
    {"video_id": "v3", "title": "third", "published_at": "2026-10-30T09:00:00Z", "channel_title": "A trader"},
    {"video_id": "v2", "title": "second", "published_at": "2026-10-29T09:00:00Z", "channel_title": "A trader"},
    {"video_id": "v1", "title": "first", "published_at": "2026-10-28T09:00:00Z", "channel_title": "A trader"},
]


@pytest.fixture
def youtube(monkeypatch):
    """The channel window, and which of its videos YouTube refuses captions for."""
    state = {"refused": set(), "no_transcript": set(), "asked": []}
    c = YouTubeConnector(source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(), channel_identifier="UCchan")

    async def _transcript(video_id, **fields):
        state["asked"].append(video_id)
        if video_id in state["refused"]:
            raise youtube_transcript.CaptionsRefused("refused", until=_UNTIL)
        if video_id in state["no_transcript"]:
            raise youtube_transcript.NoTranscript(video_id, "Whisper produced nothing")
        return [RawMessage(stable_id=f"youtube:{video_id}:full", text="transcript", content_type="video_transcript", **fields)]

    fake_youtube_window(monkeypatch, c, WINDOW)
    monkeypatch.setattr(youtube_transcript, "video_transcript_messages", _transcript)
    state["connector"] = c
    return state


async def _fetched(connector, stable_id=None) -> list[RawMessage]:
    return [m async for m in connector.fetch_new(FetchPosition(stable_id=stable_id))]


class TestARefusedVideo:
    async def test_comes_out_as_the_video_itself_in_its_place(self, youtube):
        youtube["refused"] = {"v2"}

        got = await _fetched(youtube["connector"])

        assert [(m.stable_id, m.content_type) for m in got] == [
            ("youtube:v1:full", "video_transcript"),
            ("youtube:v2", "youtube_video"),
            ("youtube:v3:full", "video_transcript"),
        ]

    async def test_carries_what_its_transcript_would_be_credited_with(self, youtube):
        youtube["refused"] = {"v2"}

        [video] = [m for m in await _fetched(youtube["connector"]) if m.content_type == "youtube_video"]

        assert video.fetch_failure.until == _UNTIL
        assert video.metadata["video_id"] == "v2"
        assert video.metadata["title"] == "second"
        assert (video.author, video.source_type) == ("A trader", "youtube")
        assert video.timestamp == datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)

    async def test_is_a_position_the_next_fetch_goes_on_from(self, youtube):
        got = await _fetched(youtube["connector"], stable_id="youtube:v2")

        assert [m.stable_id for m in got] == ["youtube:v3:full"]


class TestFetchingARefusedVideoAgain:
    async def test_once_youtube_hands_its_captions_over_it_is_its_transcript(self, youtube):
        got = await youtube["connector"].fetch_again("youtube:v2")

        assert [(m.stable_id, m.content_type) for m in got] == [("youtube:v2:full", "video_transcript")]
        assert got[0].metadata["title"] == "second"

    async def test_while_youtube_still_refuses_the_refusal_is_raised(self, youtube):
        youtube["refused"] = {"v2"}

        with pytest.raises(youtube_transcript.CaptionsRefused):
            await youtube["connector"].fetch_again("youtube:v2")

    async def test_one_part_of_a_video_asked_for_during_a_block_is_refused_not_gone(self, youtube):
        # Read as gone, it would be given up at once: a video lost to a block.
        youtube["refused"] = {"v2"}

        with pytest.raises(youtube_transcript.CaptionsRefused):
            await youtube["connector"].fetch_one("youtube:v2:full")

    async def test_a_video_no_longer_in_the_window_is_gone(self, youtube):
        assert await youtube["connector"].fetch_again("youtube:v9") is None


class TestAVideoWithNoTranscript:
    """#126: handed on carrying why, so it is recorded and the fetch moves past it."""

    async def test_comes_out_as_the_video_itself_carrying_why(self, youtube):
        youtube["no_transcript"] = {"v2"}

        got = await _fetched(youtube["connector"])

        assert [m.stable_id for m in got] == ["youtube:v1:full", "youtube:v2", "youtube:v3:full"]
        assert isinstance(got[1].fetch_failure, youtube_transcript.NoTranscript)

    async def test_fetched_again_it_is_still_a_failure(self, youtube):
        youtube["no_transcript"] = {"v2"}

        with pytest.raises(youtube_transcript.NoTranscript):
            await youtube["connector"].fetch_again("youtube:v2")
