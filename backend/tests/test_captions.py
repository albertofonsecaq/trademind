"""
Tests for #124 — a video's captions, through youtube-transcript-api 1.x.

Which captions are taken is unchanged from 0.6: made by a person before made
by YouTube, English or Spanish before anything else, and any language at all
before none. What is new is that a YouTube refusing our requests no longer
looks like a video without captions: on 2026-10-31 a channel had ingested
nothing for three days because every request got 429, and the log said "No
transcript available" for each of its 79 videos (#125 acts on it).

Only the library's network call is replaced: the transcript list it hands back
is the library's own, so the choosing is the library's real choosing.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from youtube_transcript_api import (
    FetchedTranscript, FetchedTranscriptSnippet, IpBlocked, RequestBlocked,
    TranscriptList, TranscriptsDisabled, VideoUnavailable,
)

from app.core.config import settings
from app.services import youtube_transcript
from app.services.youtube_transcript import CaptionsRefused, fetch_captions
from tests.conftest import fake_refusal_store

VIDEO = "dQw4w9WgXcQ"


class _Captions:
    """One language's captions, as YouTube lists them."""

    def __init__(self, language_code: str, *, generated: bool):
        self.language_code = language_code
        self.is_generated = generated

    def fetch(self) -> FetchedTranscript:
        return FetchedTranscript(
            snippets=[FetchedTranscriptSnippet(text=self.label, start=1.5, duration=2.0)],
            video_id=VIDEO, language=self.language_code,
            language_code=self.language_code, is_generated=self.is_generated,
        )

    @property
    def label(self) -> str:
        return f"{self.language_code} {'generated' if self.is_generated else 'manual'}"


@pytest.fixture
def youtube(monkeypatch):
    """What YouTube lists for the video, or the error it answers with."""
    state = {"listed": [], "error": None, "asked": 0}

    class _Api:
        def list(self, video_id):
            state["asked"] += 1
            if state["error"]:
                raise state["error"]
            return TranscriptList(
                video_id,
                {c.language_code: c for c in state["listed"] if not c.is_generated},
                {c.language_code: c for c in state["listed"] if c.is_generated},
                [],
            )

    monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _Api)
    # Each test starts with YouTube not refusing, at a moment it controls.
    clock = {"now": datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(youtube_transcript, "now_utc", lambda: clock["now"])
    state["clock"] = clock
    state["refusals"] = fake_refusal_store(monkeypatch)
    return state


async def _taken(youtube, *listed) -> str:
    """The label of the captions fetch_captions took, out of those listed."""
    youtube["listed"] = list(listed)
    segments = await fetch_captions(VIDEO)
    return segments[0]["text"]


class TestWhichCaptionsAreTaken:
    async def test_made_by_a_person_before_made_by_youtube(self, youtube):
        taken = await _taken(youtube, _Captions("en", generated=True), _Captions("es", generated=False))
        assert taken == "es manual"

    async def test_made_by_youtube_in_english_or_spanish_before_a_person_in_another_language(self, youtube):
        taken = await _taken(youtube, _Captions("fr", generated=False), _Captions("es", generated=True))
        assert taken == "es generated"

    async def test_any_language_before_none(self, youtube):
        assert await _taken(youtube, _Captions("pt", generated=True)) == "pt generated"

    async def test_they_come_back_as_text_start_and_duration(self, youtube):
        youtube["listed"] = [_Captions("en", generated=False)]
        assert await fetch_captions(VIDEO) == [{"text": "en manual", "start": 1.5, "duration": 2.0}]


class TestNoCaptions:
    @pytest.mark.parametrize("error", [TranscriptsDisabled(VIDEO), VideoUnavailable(VIDEO)])
    async def test_a_video_without_captions_has_none(self, youtube, error):
        youtube["error"] = error
        assert await fetch_captions(VIDEO) is None

    async def test_a_video_listing_no_captions_at_all_has_none(self, youtube):
        assert await fetch_captions(VIDEO) is None


class TestYouTubeRefusingIsNotNoCaptions:
    @pytest.mark.parametrize("error", [IpBlocked(VIDEO), RequestBlocked(VIDEO)])
    async def test_a_refusal_is_said_as_one(self, youtube, error):
        youtube["error"] = error
        with pytest.raises(CaptionsRefused):
            await fetch_captions(VIDEO)

    async def test_a_refusal_while_fetching_the_chosen_captions_is_one_too(self, youtube):
        class _Refused(_Captions):
            def fetch(self):
                raise IpBlocked(VIDEO)

        youtube["listed"] = [_Refused("en", generated=False)]
        with pytest.raises(CaptionsRefused):
            await fetch_captions(VIDEO)


class TestARefusalIsWaitedOut:
    """#125: once YouTube refuses, it is not asked again until the cool-down passes."""

    @pytest.fixture
    async def refused(self, youtube, monkeypatch):
        monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
        youtube["error"] = IpBlocked(VIDEO)
        with pytest.raises(CaptionsRefused):
            await fetch_captions(VIDEO)
        youtube["error"] = None
        youtube["listed"] = [_Captions("en", generated=False)]
        return youtube

    async def test_no_request_is_made_during_the_cool_down(self, refused):
        refused["clock"]["now"] += timedelta(minutes=59)
        with pytest.raises(CaptionsRefused):
            await fetch_captions("another")
        assert refused["asked"] == 1

    async def test_the_refusal_says_when_it_ends(self, refused):
        with pytest.raises(CaptionsRefused) as raised:
            await fetch_captions("another")
        assert raised.value.until == datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)

    async def test_youtube_is_asked_again_once_it_has_passed(self, refused):
        refused["clock"]["now"] += timedelta(minutes=60)
        assert await fetch_captions(VIDEO) == [{"text": "en manual", "start": 1.5, "duration": 2.0}]
        assert refused["asked"] == 2

    async def test_one_warning_says_so_for_the_whole_block(self, youtube, monkeypatch, caplog):
        monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
        youtube["error"] = IpBlocked(VIDEO)
        with caplog.at_level(logging.WARNING, logger=youtube_transcript.__name__):
            for video in ("a", "b", "c"):
                with pytest.raises(CaptionsRefused):
                    await fetch_captions(video)
        [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert "10:00" in warning.getMessage()


class TestARefusalIsSharedByEveryFetch:
    """#130: the refusal is where every fetch and a restart see it, not in one process."""

    async def test_one_recorded_by_another_fetch_or_before_a_restart_is_waited_out(self, youtube):
        youtube["refusals"]["youtube"] = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
        youtube["listed"] = [_Captions("en", generated=False)]

        with pytest.raises(CaptionsRefused) as raised:
            await fetch_captions(VIDEO)
        assert raised.value.until == datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
        assert youtube["asked"] == 0

    async def test_a_refusal_here_is_recorded_for_the_others(self, youtube, monkeypatch):
        monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
        youtube["error"] = IpBlocked(VIDEO)

        with pytest.raises(CaptionsRefused):
            await fetch_captions(VIDEO)
        assert youtube["refusals"]["youtube"] == datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
