"""
Tests for #153 — a broken YouTube proxy is our failure, not YouTube's refusal
and not the video's.

A proxy that is down, unreachable or rejects our credentials (407) says nothing
about YouTube or the video. Read as "no captions" it fell through to Whisper,
failed again and spent one of the video's three attempts, so three fetches of a
broken proxy gave up the backlog; read as a refusal it would arm YouTube's
cool-down for something YouTube never said. So, like a stale yt-dlp (ADR 0011),
it counts no attempt, holds no other request to YouTube up, and waits an hour
at a time with a reason that names the setting to fix — never the URL.

The errors here are the ones the installed libraries really raise, captured
against a local proxy that refuses connections or answers 407: requests'
ProxyError on the caption request, and on the audio download a yt-dlp that
warns of the proxy and then fails as if its extractor were out of date.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
from pydantic import SecretStr
from youtube_transcript_api import IpBlocked, TranscriptsDisabled

from app.connectors.youtube import YouTubeConnector
from app.core.config import settings
from app.models.evidence_item import EvidenceItem
from app.models.failed_message import FailedMessage
from app.models.source_config import SourceConfig
from app.services import ingestion_pipeline, transcription_service, youtube_refusal, youtube_transcript
from tests.conftest import FakeSession, fake_refusal_store, fake_youtube_window

_PROXY = "http://trader-user:s3cret-pass@proxy.example.net:8080"
_SECRET_PARTS = [_PROXY, "trader-user", "s3cret-pass", "proxy.example.net"]

# What requests raises on a caption request when the proxy answers 407 ...
_REJECTED = requests.exceptions.ProxyError(
    "HTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded with url: "
    "/watch?v=v1 (Caused by ProxyError('Unable to connect to proxy', "
    "OSError('Tunnel connection failed: 407 Proxy Authentication Required')))"
)
# ... and when nothing is listening where the proxy should be.
_UNREACHABLE = requests.exceptions.ProxyError(
    "HTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded with url: "
    "/watch?v=v1 (Caused by ProxyError('Unable to connect to proxy', "
    "NewConnectionError(\"HTTPSConnection(host='proxy.example.net', port=8080): "
    "Failed to establish a new connection: [Errno 61] Connection refused\")))"
)

# ... and when the proxy is reached but answers the tunnel to YouTube with an
# error of its own, as an overloaded or misconfigured one does.
_TUNNEL_REFUSED = requests.exceptions.ProxyError(
    "HTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded with url: "
    "/watch?v=v1 (Caused by ProxyError('Unable to connect to proxy', "
    "OSError('Tunnel connection failed: 503 Service Unavailable')))"
)

# A SOCKS proxy raises no ProxyError of requests' own: what reaching it fails
# with is a plain connection error, from a SOCKS connection pool ...
_SOCKS_PROXY = "socks5h://trader-user:s3cret-pass@proxy.example.net:1080"
_SOCKS_UNREACHABLE = requests.exceptions.ConnectionError(
    "SOCKSHTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded with url: "
    "/watch?v=v1 (Caused by NewConnectionError('<urllib3.contrib.socks.SOCKSHTTPSConnection "
    "object at 0x10f2c3a10>: Failed to establish a new connection: [Errno 61] Connection refused'))"
)
# ... and, while PySocks is not installed, what every request through it fails with.
_SOCKS_UNSUPPORTED = requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support.")


def _set_proxy(monkeypatch, url):
    monkeypatch.setattr(settings, "YOUTUBE_PROXY_URL", SecretStr(url))


class TestReadingAProxyFailure:
    @pytest.mark.parametrize("error, says", [
        (_REJECTED, "credentials"),
        (_UNREACHABLE, "could not be reached"),
    ])
    def test_names_the_setting_to_fix_and_nothing_of_the_url(self, monkeypatch, error, says):
        _set_proxy(monkeypatch, _PROXY)

        why = youtube_refusal.proxy_failed(error)

        assert why is not None
        assert says in why
        assert "YOUTUBE_PROXY_URL" in why
        for part in _SECRET_PARTS:
            assert part not in why

    def test_a_tunnel_the_proxy_refuses_is_told_apart_from_one_it_cannot_be_reached_for(
        self, monkeypatch
    ):
        _set_proxy(monkeypatch, _PROXY)

        why = youtube_refusal.proxy_failed(_TUNNEL_REFUSED)

        assert why is not None
        assert "answered" in why and "could not be reached" not in why
        assert "YOUTUBE_PROXY_URL" in why

    def test_requests_saying_its_proxy_failed_is_enough_whatever_the_words(self, monkeypatch):
        _set_proxy(monkeypatch, _PROXY)

        why = youtube_refusal.proxy_failed(requests.exceptions.ProxyError("something new"))

        assert why is not None
        assert "YOUTUBE_PROXY_URL" in why

    @pytest.mark.parametrize("error, says", [
        (_SOCKS_UNREACHABLE, "could not be reached"),
        (_SOCKS_UNSUPPORTED, "requests[socks]"),
    ])
    def test_a_socks_proxy_failing_is_one(self, monkeypatch, error, says):
        _set_proxy(monkeypatch, _SOCKS_PROXY)

        why = youtube_refusal.proxy_failed(error)

        assert why is not None
        assert says in why
        for part in _SECRET_PARTS:
            assert part not in why

    def test_a_connection_error_through_an_http_proxy_is_not_one_by_its_type_alone(
        self, monkeypatch
    ):
        # Through an HTTP proxy, requests says the proxy failed as a ProxyError;
        # a connection broken after the tunnel is up is not the proxy's to own.
        _set_proxy(monkeypatch, _PROXY)

        assert youtube_refusal.proxy_failed(
            requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected())")
        ) is None

    @pytest.mark.parametrize("error", [
        _UNREACHABLE, _TUNNEL_REFUSED, _SOCKS_UNREACHABLE, _SOCKS_UNSUPPORTED,
        requests.exceptions.ProxyError("something new"),
    ])
    def test_is_not_possible_without_a_proxy_set(self, monkeypatch, error):
        # Nothing of ours stands between us and YouTube to fail.
        _set_proxy(monkeypatch, "")

        assert youtube_refusal.proxy_failed(error) is None

    def test_a_refusal_is_asked_first(self, monkeypatch):
        # ADR 0009: a refusal met through the proxy is still a refusal.
        _set_proxy(monkeypatch, _PROXY)

        assert youtube_refusal.proxy_failed(IpBlocked("v1")) is None

    @pytest.mark.parametrize("error", [
        RuntimeError("ERROR: [youtube] v1: HTTP Error 500: Internal Server Error"),
        RuntimeError("ERROR: [youtube] v1: This live event has ended"),
    ])
    def test_and_nothing_else_is_one(self, monkeypatch, error):
        _set_proxy(monkeypatch, _PROXY)

        assert youtube_refusal.proxy_failed(error) is None


# --- the caption request ---------------------------------------------------------

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
    """
    The captions library, faked where it is asked for a video's transcripts,
    with a proxy set. `captions` is what the library does with the request.
    """
    state = {"captions": lambda video_id: _TranscriptList()}

    class _FakeApi:
        def __init__(self, proxy_config=None, http_client=None):
            pass

        def list(self, video_id):
            return state["captions"](video_id)

    monkeypatch.setattr(youtube_transcript, "YouTubeTranscriptApi", _FakeApi)
    state["refusals"] = fake_refusal_store(monkeypatch)
    _set_proxy(monkeypatch, _PROXY)
    return state


@pytest.fixture
def youtube(library, monkeypatch):
    """The captions library, and Whisper faked whole; `whisper_asked` counts the fallbacks."""
    library["whisper_asked"] = 0

    async def _whisper(video_url):
        library["whisper_asked"] += 1
        return None

    monkeypatch.setattr(transcription_service, "transcribe_youtube_video", _whisper)
    return library


def _raises(error):
    def _fails(*args):
        raise error
    return _fails


async def _messages(video_id="v1"):
    return await youtube_transcript.video_transcript_messages(
        video_id, source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
        author=None, channel="@chan", timestamp=datetime.now(timezone.utc), source_type="youtube",
    )


class TestAProxyFailureOnTheCaptionRequest:
    @pytest.mark.parametrize("error", [_REJECTED, _UNREACHABLE])
    async def test_costs_the_video_no_attempt_and_waits_an_hour(self, youtube, error):
        youtube["captions"] = _raises(error)

        with pytest.raises(youtube_transcript.ProxyFailed) as raised:
            await _messages()

        assert not raised.value.counts_an_attempt()
        assert not raised.value.can_never_succeed()
        assert raised.value.wait_after(0) == raised.value.wait_after(5) == timedelta(hours=1)

    @pytest.mark.parametrize("error", [_SOCKS_UNREACHABLE, _SOCKS_UNSUPPORTED])
    async def test_through_a_socks_proxy_costs_the_video_no_attempt(
        self, youtube, monkeypatch, error
    ):
        _set_proxy(monkeypatch, _SOCKS_PROXY)
        youtube["captions"] = _raises(error)

        with pytest.raises(youtube_transcript.ProxyFailed) as raised:
            await _messages()

        assert not raised.value.counts_an_attempt()
        assert youtube["whisper_asked"] == 0

    async def test_is_not_read_as_no_captions_so_whisper_is_not_asked(self, youtube):
        youtube["captions"] = _raises(_REJECTED)

        with pytest.raises(youtube_transcript.ProxyFailed):
            await _messages()

        assert youtube["whisper_asked"] == 0

    async def test_records_no_refusal_of_youtube(self, youtube):
        youtube["captions"] = _raises(_UNREACHABLE)

        with pytest.raises(youtube_transcript.ProxyFailed):
            await _messages()

        assert youtube["refusals"] == {}

    async def test_says_the_setting_to_fix_and_nothing_of_the_url(self, youtube, caplog):
        youtube["captions"] = _raises(_REJECTED)

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(youtube_transcript.ProxyFailed) as raised:
                await _messages()

        assert "YOUTUBE_PROXY_URL" in str(raised.value)
        for part in _SECRET_PARTS:
            assert part not in str(raised.value)
            assert part not in caplog.text

    async def test_without_a_proxy_set_the_same_error_is_still_no_captions(self, youtube, monkeypatch):
        # Nothing of ours to fail: an error from the network library reads as before.
        _set_proxy(monkeypatch, "")
        youtube["captions"] = _raises(_UNREACHABLE)

        with pytest.raises(youtube_transcript.NoTranscript):
            await _messages()

        assert youtube["whisper_asked"] == 1


# --- the audio download for Whisper ----------------------------------------------

# What yt-dlp really does through a broken proxy: it warns of the proxy on each
# page it fails to download, then fails in the words of a stale extractor.
_STALE_WORDS = (
    "ERROR: [youtube] v1: Failed to extract any player response; please report this issue on  "
    "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
    "Confirm you are on the latest version using  yt-dlp -U"
)
_WARNED_REJECTED = (
    "[youtube] Unable to download webpage: ('Unable to connect to proxy', "
    "OSError('Tunnel connection failed: 407 Proxy Authentication Required'))"
)
_WARNED_UNREACHABLE = (
    "[youtube] Unable to download API page: ('Unable to connect to proxy', "
    "NewConnectionError(\"HTTPSConnection(host='proxy.example.net', port=8080): Failed to "
    f"establish a new connection: [Errno 61] Connection refused\")) (caused by ProxyError({_PROXY!r}))"
)


@pytest.fixture
def audio(library, monkeypatch):
    """
    A video with no captions, so its audio is downloaded for Whisper: yt-dlp
    faked where it is handed its options, with what it warns of on the way and
    how it fails at the end. OpenAI is never reached.
    """
    state = {"warns": [], "fails_with": _STALE_WORDS}

    def _download_audio(video_url, options):
        for warning in state["warns"]:
            options["logger"].warning(warning)
        raise RuntimeError(state["fails_with"])

    library["captions"] = _raises(TranscriptsDisabled("v1"))
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(transcription_service, "_download_audio", _download_audio)
    state["refusals"] = library["refusals"]
    return state


class TestAProxyFailureOnTheAudioDownload:
    @pytest.mark.parametrize("warned, says", [
        (_WARNED_REJECTED, "credentials"),
        (_WARNED_UNREACHABLE, "could not be reached"),
    ])
    async def test_is_the_proxy_and_not_a_stale_extractor(self, audio, warned, says):
        audio["warns"] = [warned, warned]

        with pytest.raises(youtube_transcript.ProxyFailed) as raised:
            await _messages()

        assert says in str(raised.value)
        assert not raised.value.counts_an_attempt()
        assert not raised.value.can_never_succeed()
        assert raised.value.wait_after(2) == timedelta(hours=1)

    async def test_records_no_refusal_of_youtube(self, audio):
        audio["warns"] = [_WARNED_UNREACHABLE]

        with pytest.raises(youtube_transcript.ProxyFailed):
            await _messages()

        assert audio["refusals"] == {}

    async def test_says_the_setting_to_fix_and_nothing_of_the_url(self, audio, caplog):
        audio["warns"] = [_WARNED_UNREACHABLE]

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(youtube_transcript.ProxyFailed) as raised:
                await _messages()

        assert "YOUTUBE_PROXY_URL" in str(raised.value)
        for part in _SECRET_PARTS:
            assert part not in str(raised.value)
            assert part not in caplog.text

    async def test_a_stale_extractor_with_the_proxy_working_is_still_one(self, audio):
        # #139: nothing said of the proxy on the way, so the words mean what they say.
        with pytest.raises(youtube_transcript.ExtractorOutOfDate):
            await _messages()

    async def test_a_proxy_that_stumbled_on_the_way_to_a_final_answer_did_not_stop_it(self, audio):
        # #137: the video's own final problem was still reached and said.
        audio["warns"] = [_WARNED_UNREACHABLE]
        audio["fails_with"] = "ERROR: [youtube] v1: This live event has ended"

        with pytest.raises(youtube_transcript.NeverTranscribable):
            await _messages()

    async def test_a_refusal_met_through_the_proxy_is_still_a_refusal(self, audio):
        # ADR 0009, 0014: asked first, so nothing here narrows what a refusal is.
        audio["warns"] = [_WARNED_REJECTED]
        audio["fails_with"] = "ERROR: [youtube] v1: Sign in to confirm you're not a bot"

        with pytest.raises(youtube_transcript.YouTubeRefused):
            await _messages()


# --- a channel source fetched through a broken proxy, and then a fixed one -----

@pytest.fixture
def channel(library, offline_pipeline, monkeypatch):
    """
    A YouTube source whose channel holds one video, fetched by the real
    connector and pipeline into a session kept in memory. Only the channel
    listing, the captions library and the paid calls are answered locally.
    """
    connector = YouTubeConnector(
        source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(), channel_identifier="UCchan"
    )
    fake_youtube_window(monkeypatch, connector, [{
        "video_id": "v1", "title": "EURUSD this week",
        "published_at": "2026-11-20T09:00:00Z", "channel_title": "A trader",
    }])
    monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda _source: connector)

    db = FakeSession()

    def _failures(_statement):
        return [
            row for row in db.added
            if isinstance(row, FailedMessage) and row not in db.deleted and row.given_up_at is None
        ]

    db.register(FailedMessage, _failures)
    source = SimpleNamespace(
        id=connector.source_config_id, content_filters={"video": True}, last_fetched_id=None,
        last_fetched_item_at=None, platform_connection_id=uuid.uuid4(),
    )
    # Where the fetch got to is kept, so the next one goes on past the video.
    db.register(SourceConfig, source)

    async def fetch():
        return await ingestion_pipeline.run_fetch_pipeline(
            db, source=source, workspace=SimpleNamespace(topic_scope="trading")
        )

    return SimpleNamespace(
        fetch=fetch, failures=lambda: _failures(None), db=db, library=library,
    )


class TestASourceFetchedThroughABrokenProxy:
    async def test_waits_unattempted_and_an_ordinary_retry_stores_it_once_the_proxy_works(self, channel):
        channel.library["captions"] = _raises(_REJECTED)

        # The first fetch, and four retries: more than the three attempts an
        # ordinary failure has before it is given up.
        for _ in range(5):
            await channel.fetch()

        [row] = channel.failures()
        assert (row.attempts, row.given_up_at) == (0, None)
        assert row.retry_after is not None
        assert "YOUTUBE_PROXY_URL" in row.error
        for part in _SECRET_PARTS:
            assert part not in row.error
        assert channel.library["refusals"] == {}

        # The proxy fixed, and nothing sent back by hand.
        channel.library["captions"] = lambda video_id: _TranscriptList()
        outcome = await channel.fetch()

        assert outcome.ingested == 1
        assert channel.failures() == []
        assert [item.stable_id for item in channel.db.added if isinstance(item, EvidenceItem)] == [
            "youtube:v1:full"
        ]
