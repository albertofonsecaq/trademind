"""
Tests for #150 — caption requests to YouTube go through a proxy when one is set.

YouTube has been blocking this machine's address, at about one caption request
an hour, so waiting longer does not end it (ADR 0010). With `YOUTUBE_PROXY_URL`
set, the captions library sends every request through that proxy; unset,
nothing changes. The URL carries the proxy's credentials, so it is never logged
or written into a reason, and a refusal met through the proxy is still a
refusal (ADR 0007, 0009, 0010).

Only the captions library is faked: what it was built with, and what it said.

And #152: the audio Whisper needs is downloaded through the same proxy, for
YouTube refuses that download for the same reason. There yt-dlp is faked where
it is handed its options, and OpenAI where it is asked to transcribe.
"""
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from pydantic import SecretStr
from youtube_transcript_api import IpBlocked

from app.core.config import Settings, settings
from app.services import refusal_store, transcription_service, youtube_proxy, youtube_transcript
from tests.conftest import fake_refusal_store

_PROXY = "http://trader-user:s3cret-pass@proxy.example.net:8080"


class _Captions:
    def to_raw_data(self):
        return [{"text": "EURUSD long", "start": 0.0, "duration": 5.0}]


class _Transcript:
    def fetch(self):
        return _Captions()


class _TranscriptList:
    def find_manually_created_transcript(self, languages):
        return _Transcript()


@pytest.fixture
def library(monkeypatch):
    """The captions library: every proxy config it was built with, and what `list` does."""
    state = {"built_with": [], "list": lambda video_id: _TranscriptList()}

    class _FakeApi:
        def __init__(self, proxy_config=None, http_client=None):
            state["built_with"].append(proxy_config)

        def list(self, video_id):
            return state["list"](video_id)

    monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _FakeApi)
    state["refusals"] = fake_refusal_store(monkeypatch)
    return state


def _set_proxy(monkeypatch, url):
    monkeypatch.setattr(settings, "YOUTUBE_PROXY_URL", SecretStr(url))


class TestWithAProxySet:
    async def test_every_caption_request_goes_through_it(self, library, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)

        await youtube_transcript.fetch_captions("v1")
        await youtube_transcript.fetch_captions("v2")

        assert [c.to_requests_dict() for c in library["built_with"]] == [
            {"http": _PROXY, "https": _PROXY},
            {"http": _PROXY, "https": _PROXY},
        ]


class TestWithoutAProxy:
    async def test_no_proxy_is_passed(self, library, monkeypatch):
        _set_proxy(monkeypatch, "")

        captions = await youtube_transcript.fetch_captions("v1")

        assert library["built_with"] == [None]
        assert captions == [{"text": "EURUSD long", "start": 0.0, "duration": 5.0}]


# Every part of the URL that would tell a reader where the proxy is or how to use it.
_SECRET_PARTS = [_PROXY, "trader-user", "s3cret-pass", "proxy.example.net"]


def _names_the_proxy(video_id):
    # An error from the network library with the proxy's own address, and with
    # it the credentials, inside its text. Not the proxy failing — that is
    # said in our own words (#153) — so it is read as no captions, and logged.
    raise requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='www.youtube.com', port=443): Read timed out. "
        f"(read timeout=None, through {_PROXY})"
    )


class TestTheProxyUrlIsNeverSaid:
    async def test_not_in_the_log_when_the_library_puts_it_in_its_error(self, library, monkeypatch, caplog):
        _set_proxy(monkeypatch, _PROXY)
        library["list"] = _names_the_proxy

        with caplog.at_level(logging.DEBUG):
            await youtube_transcript.fetch_captions("v1")

        assert caplog.records, "the failure is still logged, without the proxy"
        for part in _SECRET_PARTS:
            assert part not in caplog.text

    def test_nor_its_password_when_the_url_encodes_it(self, monkeypatch):
        # A password with an @ in it is written %40 in the URL, and an error
        # may say it either way.
        _set_proxy(monkeypatch, "http://trader-user:p%40ss-word@proxy.example.net:8080")

        said = youtube_proxy.scrub("could not log in as trader-user with p@ss-word or p%40ss-word")

        assert "trader-user" not in said
        assert "p@ss-word" not in said and "p%40ss-word" not in said

    def test_not_in_the_settings_when_they_are_printed(self, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)

        for part in _SECRET_PARTS:
            assert part not in repr(settings) and part not in str(settings)


class TestARefusalMetThroughTheProxy:
    """ADR 0007, 0009, 0010: a proxy changes where we ask from, not what a refusal means."""

    async def test_is_still_recorded_and_waited_out(self, library, monkeypatch, caplog):
        _set_proxy(monkeypatch, _PROXY)
        monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
        library["list"] = _blocked

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(youtube_transcript.YouTubeRefused) as raised:
                await youtube_transcript.fetch_captions("v1")

        assert library["refusals"][refusal_store.YOUTUBE].until == raised.value.until
        for part in _SECRET_PARTS:
            assert part not in str(raised.value)
            assert part not in caplog.text

    async def test_holds_the_next_request_up_without_asking(self, library, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)
        library["list"] = _blocked
        with pytest.raises(youtube_transcript.YouTubeRefused):
            await youtube_transcript.fetch_captions("v1")

        with pytest.raises(youtube_transcript.YouTubeRefused):
            await youtube_transcript.fetch_captions("v2")

        assert len(library["built_with"]) == 1


def _blocked(video_id):
    raise IpBlocked(video_id)


class TestTheSetting:
    def test_is_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_PROXY_URL", raising=False)

        assert Settings(_env_file=None).YOUTUBE_PROXY_URL.get_secret_value() == ""

    def test_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_PROXY_URL", _PROXY)

        assert Settings(_env_file=None).YOUTUBE_PROXY_URL.get_secret_value() == _PROXY

    def test_is_documented_in_the_example_environment(self):
        example = Path(__file__).parents[1] / ".env.example"

        assert "\nYOUTUBE_PROXY_URL=\n" in example.read_text()


# --- #152: the audio Whisper needs is downloaded through the same proxy ---------

_VIDEO = "https://www.youtube.com/watch?v=v1"


@pytest.fixture
def ytdlp(monkeypatch):
    """
    yt-dlp, faked where it is handed its options: every set of options it was
    given, and what downloading does — by default, an mp3 where it was told to
    write one. Whisper is faked too, so nothing leaves the machine.
    """
    state = {"given": []}

    def _writes_an_mp3(video_url, options):
        Path(options["outtmpl"] % {"ext": "mp3"}).write_bytes(b"audio")

    state["download"] = _writes_an_mp3

    def _download_audio(video_url, options):
        state["given"].append(options)
        state["download"](video_url, options)

    class _Heard:
        segments = []
        duration = 60.0

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(transcription_service, "_download_audio", _download_audio)
    monkeypatch.setattr(transcription_service, "_ask_whisper", lambda audio_path: _Heard(), raising=False)
    return state


class TestTheAudioForWhisper:
    async def test_is_downloaded_through_the_proxy_when_one_is_set(self, ytdlp, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)

        await transcription_service.transcribe_youtube_video(_VIDEO)

        assert [given.get("proxy") for given in ytdlp["given"]] == [_PROXY]

    async def test_is_downloaded_as_before_when_none_is(self, ytdlp, monkeypatch):
        _set_proxy(monkeypatch, "")

        await transcription_service.transcribe_youtube_video(_VIDEO)

        assert len(ytdlp["given"]) == 1
        assert "proxy" not in ytdlp["given"][0]


def _ytdlp_says(words):
    """A download that fails with yt-dlp's `words`, as its DownloadError would say them."""
    def _fails(video_url, options):
        raise RuntimeError(words)
    return _fails


class TestTheProxyUrlIsNeverSaidByTheDownload:
    async def test_not_in_the_reason_or_the_log_when_yt_dlp_puts_it_in_its_error(
        self, ytdlp, monkeypatch, caplog
    ):
        # Not the proxy failing — that is said in our own words (#153) — but
        # yt-dlp's words passed on, which name the proxy here.
        _set_proxy(monkeypatch, _PROXY)
        ytdlp["download"] = _ytdlp_says(
            "ERROR: [youtube] v1: Unable to download webpage: "
            f"HTTP Error 500: Internal Server Error (through {_PROXY})"
        )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(transcription_service.WhisperGaveNothing) as raised:
                await transcription_service.transcribe_youtube_video(_VIDEO)

        assert caplog.records, "the failure is still logged, without the proxy"
        assert "Internal Server Error" in str(raised.value)
        for part in _SECRET_PARTS:
            assert part not in str(raised.value)
            assert part not in caplog.text


class TestTheDownloadsOwnErrorIsKeptOnlyWhenNoProxyCanBeInIt:
    """
    yt-dlp's original error is chained on for its traceback, unless a proxy is
    set: then its words may name the proxy, and nothing it says is passed on.
    """

    @pytest.mark.parametrize("words, raises", [
        ("ERROR: [youtube] v1: HTTP Error 500: Internal Server Error",
         transcription_service.WhisperGaveNothing),
        ("ERROR: [youtube] v1: HTTP Error 429: Too Many Requests",
         transcription_service.DownloadRefused),
    ])
    async def test_chained_on_without_a_proxy(self, ytdlp, monkeypatch, words, raises):
        _set_proxy(monkeypatch, "")
        ytdlp["download"] = _ytdlp_says(words)

        with pytest.raises(raises) as raised:
            await transcription_service.transcribe_youtube_video(_VIDEO)

        assert isinstance(raised.value.__cause__, RuntimeError)
        assert str(raised.value.__cause__) == words

    @pytest.mark.parametrize("words, raises", [
        (f"ERROR: [youtube] v1: HTTP Error 500: Internal Server Error (through {_PROXY})",
         transcription_service.WhisperGaveNothing),
        (f"ERROR: [youtube] v1: HTTP Error 429: Too Many Requests (through {_PROXY})",
         transcription_service.DownloadRefused),
    ])
    async def test_left_off_with_one(self, ytdlp, monkeypatch, words, raises):
        _set_proxy(monkeypatch, _PROXY)
        ytdlp["download"] = _ytdlp_says(words)

        with pytest.raises(raises) as raised:
            await transcription_service.transcribe_youtube_video(_VIDEO)

        assert raised.value.__cause__ is None
        assert raised.value.__suppress_context__


class TestARefusalOfTheDownloadMetThroughTheProxy:
    """ADR 0009: YouTube refusing the audio is the same refusal, wherever we asked from."""

    _REFUSED = (
        "ERROR: [youtube] v1: Sign in to confirm you’re not a bot. "
        f"Requested through {_PROXY} (proxy.example.net)"
    )

    async def test_is_still_a_refusal_and_does_not_say_the_proxy(self, ytdlp, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)
        ytdlp["download"] = _ytdlp_says(self._REFUSED)

        with pytest.raises(transcription_service.DownloadRefused) as raised:
            await transcription_service.transcribe_youtube_video(_VIDEO)

        assert "not a bot" in str(raised.value)
        for part in _SECRET_PARTS:
            assert part not in str(raised.value)

    async def test_is_recorded_and_waited_out_without_the_proxy_in_its_reason(
        self, ytdlp, monkeypatch, caplog
    ):
        _set_proxy(monkeypatch, _PROXY)
        monkeypatch.setattr(settings, "YOUTUBE_REFUSAL_COOLDOWN_MINUTES", 60)
        refusals = fake_refusal_store(monkeypatch)
        reasons = []
        recorded = refusal_store.refuse

        async def _refuse(platform, reason, **wait):
            reasons.append(reason)
            return await recorded(platform, reason, **wait)

        monkeypatch.setattr(refusal_store, "refuse", _refuse)

        async def _no_captions(video_id):
            return None

        monkeypatch.setattr(youtube_transcript, "fetch_captions", _no_captions)
        ytdlp["download"] = _ytdlp_says(self._REFUSED)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(youtube_transcript.YouTubeRefused) as raised:
                await youtube_transcript.video_transcript_messages(
                    "v1", source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
                    author=None, channel="@chan", timestamp=datetime.now(timezone.utc),
                    source_type="youtube",
                )

        assert refusals[refusal_store.YOUTUBE].until == raised.value.until
        assert len(reasons) == 1
        for part in _SECRET_PARTS:
            assert part not in reasons[0]
            assert part not in str(raised.value)
            assert part not in caplog.text
