"""
Tests for #150 — caption requests to YouTube go through a proxy when one is set.

YouTube has been blocking this machine's address, at about one caption request
an hour, so waiting longer does not end it (ADR 0010). With `YOUTUBE_PROXY_URL`
set, the captions library sends every request through that proxy; unset,
nothing changes. The URL carries the proxy's credentials, so it is never logged
or written into a reason, and a refusal met through the proxy is still a
refusal (ADR 0007, 0009, 0010).

Only the captions library is faked: what it was built with, and what it said.
"""
import logging
from pathlib import Path

import pytest
import requests
from pydantic import SecretStr
from youtube_transcript_api import IpBlocked

from app.core.config import Settings, settings
from app.services import refusal_store, youtube_proxy, youtube_transcript
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


def _proxy_failed(video_id):
    # What requests says when the proxy cannot be reached: the proxy's own
    # address, and with it the credentials, inside the error's text.
    raise requests.exceptions.ProxyError(
        "HTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded "
        f"(Caused by ProxyError('Unable to connect to proxy {_PROXY}', "
        "NewConnectionError('proxy.example.net:8080: Failed to establish a new connection')))"
    )


class TestTheProxyUrlIsNeverSaid:
    async def test_not_in_the_log_when_the_library_puts_it_in_its_error(self, library, monkeypatch, caplog):
        _set_proxy(monkeypatch, _PROXY)
        library["list"] = _proxy_failed

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
