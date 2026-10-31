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
import pytest
from youtube_transcript_api import (
    FetchedTranscript, FetchedTranscriptSnippet, IpBlocked, RequestBlocked,
    TranscriptList, TranscriptsDisabled, VideoUnavailable,
)

from app.services import youtube_transcript
from app.services.youtube_transcript import CaptionsRefused, fetch_captions

VIDEO = "dQw4w9WgXcQ"


class _Captions:
    """One language's captions, as YouTube lists them."""

    def __init__(self, language_code: str, *, generated: bool):
        self.language_code = language_code
        self.is_generated = generated

    def fetch(self) -> FetchedTranscript:
        return FetchedTranscript(
            snippets=[FetchedTranscriptSnippet(text=f"{self.label} line", start=1.5, duration=2.0)],
            video_id=VIDEO, language=self.language_code,
            language_code=self.language_code, is_generated=self.is_generated,
        )

    @property
    def label(self) -> str:
        return f"{self.language_code} {'generated' if self.is_generated else 'manual'}"


@pytest.fixture
def youtube(monkeypatch):
    """What YouTube lists for the video, or the error it answers with."""
    state = {"listed": [], "error": None}

    class _Api:
        def list(self, video_id):
            if state["error"]:
                raise state["error"]
            return TranscriptList(
                video_id,
                {c.language_code: c for c in state["listed"] if not c.is_generated},
                {c.language_code: c for c in state["listed"] if c.is_generated},
                [],
            )

    monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _Api)
    return state


async def _taken(youtube, *listed) -> str:
    youtube["listed"] = list(listed)
    segments = await fetch_captions(VIDEO)
    return segments[0]["text"].removesuffix(" line")


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
        assert await fetch_captions(VIDEO) == [{"text": "en manual line", "start": 1.5, "duration": 2.0}]


class TestNoCaptions:
    @pytest.mark.parametrize("error", [TranscriptsDisabled(VIDEO), VideoUnavailable(VIDEO)])
    async def test_a_video_without_captions_has_none(self, youtube, error):
        youtube["error"] = error
        assert await fetch_captions(VIDEO) is None

    async def test_a_video_listing_no_captions_at_all_has_none(self, youtube):
        assert await fetch_captions(VIDEO) is None


class TestYouTubeRefusingIsNotNoCaptions:
    # The library raises IpBlocked for a 429.
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
