"""
Tests for #151 — transcript requests to YouTube are spaced out.

A backfill over months of a channel's videos asked YouTube for one transcript
after another as fast as it could answer, which is the burst a platform refuses.
Every request for a transcript — captions, or the audio Whisper needs — now
waits a minimum gap, plus a little jitter, after the one before it, whichever
poll, backfill or retry made it: the pace belongs to the process.

The clock the pace reads is the fake one `conftest.youtube_pace` installs, so
nothing here really sleeps.
"""
import asyncio
import threading
from pathlib import Path

import pytest
from youtube_transcript_api import IpBlocked, TranscriptsDisabled

from app.core.config import settings
from app.services import transcription_service, youtube_transcript
from app.services import youtube_pace as youtube_pace_module
from app.services.youtube_transcript import YouTubeRefused, fetch_captions
from tests.conftest import fake_refusal_store

GAP = 5.0
TRANSCRIBING = 600.0


@pytest.fixture
def youtube(monkeypatch, youtube_pace):
    """YouTube answering every request with no captions, noting when it was asked."""
    asked: list[float] = []

    class _Api:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            asked.append(youtube_pace.now)
            raise TranscriptsDisabled(video_id)

    monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _Api)
    monkeypatch.setattr(settings, "YOUTUBE_REQUEST_GAP_SECONDS", GAP)
    monkeypatch.setattr(settings, "YOUTUBE_REQUEST_JITTER_SECONDS", 0.0)
    fake_refusal_store(monkeypatch)
    return asked


class TestRequestsAreSpacedOut:
    async def test_two_made_one_after_the_other_are_the_gap_apart(self, youtube):
        await fetch_captions("a")
        await fetch_captions("b")

        assert youtube[1] - youtube[0] >= GAP

    async def test_two_made_by_fetches_running_at_once_are_the_gap_apart(self, youtube):
        await asyncio.gather(fetch_captions("a"), fetch_captions("b"))

        assert len(youtube) == 2
        assert youtube[1] - youtube[0] >= GAP

    async def test_the_first_is_not_held_up(self, youtube, youtube_pace):
        await fetch_captions("a")

        assert youtube_pace.waited == []

    async def test_one_made_long_after_the_last_does_not_wait(self, youtube, youtube_pace):
        await fetch_captions("a")
        youtube_pace.now += GAP

        await fetch_captions("b")

        assert youtube_pace.waited == []

    async def test_the_jitter_is_added_to_the_gap(self, youtube, youtube_pace, monkeypatch):
        # Drawn up to the setting: here the most it can be, so it shows.
        monkeypatch.setattr(settings, "YOUTUBE_REQUEST_JITTER_SECONDS", 2.0)
        monkeypatch.setattr(youtube_pace_module, "_pace", youtube_pace_module.Pace(
            clock=youtube_pace.monotonic, sleep=youtube_pace.sleep, jitter=lambda low, high: high,
        ))

        await fetch_captions("a")
        await fetch_captions("b")

        assert youtube[1] - youtube[0] == GAP + 2.0


class TestACoolDownIsNotPacedOut:
    """While a refusal is waited out nothing is asked, so there is no turn to wait for."""

    @pytest.fixture
    def refusing(self, youtube, monkeypatch):
        """YouTube refusing every request from now on."""
        class _Refusing:
            def __init__(self, proxy_config=None):
                pass

            def list(self, video_id):
                youtube.append(video_id)
                raise IpBlocked(video_id)

        monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _Refusing)
        return youtube

    async def test_a_request_a_cool_down_holds_does_not_wait_the_gap_first(self, refusing, youtube_pace):
        with pytest.raises(YouTubeRefused):
            await fetch_captions("a")

        with pytest.raises(YouTubeRefused):
            await fetch_captions("b")

        assert youtube_pace.waited == []
        assert len(refusing) == 1

    async def test_one_waiting_its_turn_behind_a_refused_request_is_not_made(
        self, refusing, youtube_pace
    ):
        results = await asyncio.gather(
            fetch_captions("a"), fetch_captions("b"), return_exceptions=True
        )

        assert all(isinstance(r, YouTubeRefused) for r in results)
        assert len(refusing) == 1
        # Nor does it wait the gap first, only to find it will not be made.
        assert youtube_pace.waited == []

    async def test_a_download_waiting_its_turn_behind_a_refused_request_is_not_made(
        self, refusing, youtube_pace, monkeypatch
    ):
        downloaded = []
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(
            transcription_service, "_download_audio",
            lambda video_url, options: downloaded.append(video_url),
        )

        results = await asyncio.gather(
            fetch_captions("a"),
            transcription_service.transcribe_youtube_video("https://www.youtube.com/watch?v=b"),
            return_exceptions=True,
        )

        assert all(isinstance(r, YouTubeRefused) for r in results)
        assert downloaded == []
        assert youtube_pace.waited == []


class TestATurnNoRequestIsMadeIn:
    """
    A turn whose request turns out not to be made — the cool-down is found in
    force once the gap has been waited — is not a request: the next one is
    spaced from the last request that was made.
    """

    class _Held(Exception):
        pass

    async def test_the_next_turn_is_spaced_from_the_last_request_made(
        self, youtube_pace, monkeypatch
    ):
        monkeypatch.setattr(settings, "YOUTUBE_REQUEST_GAP_SECONDS", GAP)
        monkeypatch.setattr(settings, "YOUTUBE_REQUEST_JITTER_SECONDS", 0.0)
        pace = youtube_pace_module.Pace(clock=youtube_pace.monotonic, sleep=youtube_pace.sleep)
        checks = iter([None, None, self._Held(), None, None])

        async def _ready():
            found = next(checks)
            if found is not None:
                raise found

        async with pace.turn(_ready):
            pass                                    # a request made
        with pytest.raises(self._Held):
            async with pace.turn(_ready):          # waits the gap, then is held
                pytest.fail("a request held up by a cool-down was made")
        async with pace.turn(_ready):
            pass

        assert youtube_pace.waited == [GAP]


class TestTheAudioForWhisperTakesItsTurnToo:
    """A video without captions asks YouTube twice in one fetch: captions, then audio."""

    @pytest.fixture
    def whisper(self, youtube, youtube_pace, monkeypatch):
        """
        Whisper configured: its download noting when YouTube was asked, and
        OpenAI's transcription after it taking ten minutes on the pace's clock.
        """
        def _download(video_url, options):
            youtube.append(youtube_pace.now)
            Path(options["outtmpl"] % {"ext": "mp3"}).write_bytes(b"audio")

        class _Heard:
            segments = []
            duration = 60.0

        def _ask_whisper(audio_path):
            youtube_pace.now += TRANSCRIBING
            return _Heard()

        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(transcription_service, "_download_audio", _download)
        monkeypatch.setattr(transcription_service, "_ask_whisper", _ask_whisper)
        return youtube

    async def test_the_audio_is_asked_for_the_gap_after_the_captions(self, whisper):
        await fetch_captions("a")
        await transcription_service.transcribe_youtube_video("https://www.youtube.com/watch?v=a")

        assert whisper[1] - whisper[0] >= GAP

    async def test_the_next_request_is_spaced_from_the_download_not_the_transcription(
        self, whisper, youtube_pace
    ):
        # Only the download asks YouTube. Holding the turn through OpenAI's
        # transcription as well would keep every caption request waiting
        # behind it, and space the next one from its end.
        await transcription_service.transcribe_youtube_video("https://www.youtube.com/watch?v=a")
        await fetch_captions("b")

        assert youtube_pace.waited == []
        assert whisper[1] - whisper[0] == TRANSCRIBING

    async def test_a_caption_request_is_not_held_up_while_whisper_transcribes(
        self, whisper, monkeypatch
    ):
        # The transcription is under way, and a caption request made meanwhile
        # is answered before it ends.
        transcribing = threading.Event()
        captions_asked = threading.Event()

        def _ask_whisper(audio_path):
            transcribing.set()
            assert captions_asked.wait(timeout=5), "the caption request waited behind the transcription"
            return type("_Heard", (), {"segments": [], "duration": 60.0})()

        monkeypatch.setattr(transcription_service, "_ask_whisper", _ask_whisper)

        async def _captions_meanwhile():
            await asyncio.get_running_loop().run_in_executor(None, transcribing.wait, 5)
            await fetch_captions("b")
            captions_asked.set()

        await asyncio.gather(
            transcription_service.transcribe_youtube_video("https://www.youtube.com/watch?v=a"),
            _captions_meanwhile(),
        )

        assert len(whisper) == 2

    async def test_nothing_waits_when_whisper_will_not_ask_youtube(self, youtube, youtube_pace, monkeypatch):
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
        await fetch_captions("a")

        with pytest.raises(transcription_service.WhisperGaveNothing):
            await transcription_service.transcribe_youtube_video("https://www.youtube.com/watch?v=a")

        assert youtube_pace.waited == []
