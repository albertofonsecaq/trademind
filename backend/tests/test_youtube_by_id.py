"""
Tests for #131 — a YouTube video is found by its own id, not only in the newest 50.

The connector found a video only by walking the channel's 50 newest uploads, so
a failed message for an older video was given up as gone although the video
was still there — which a long YouTube block (#125) makes likely — and a
backfill could not reach anything older than those 50 (#127).

Only the YouTube Data API client is replaced, and it records every call: the
quota each path costs is asserted here rather than estimated.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.connectors import youtube as youtube_module
from app.connectors.base import FetchPosition, RawMessage
from app.connectors.youtube import YouTubeConnector
from app.core.config import settings
from app.services import youtube_transcript

_NEWEST = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


def _published(n: int) -> datetime:
    """Video n of the channel, newest first, one a day."""
    return _NEWEST - timedelta(days=n)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _snippet(n: int) -> dict:
    return {
        "title": f"lesson {n}",
        "publishedAt": _iso(_published(n)),
        "description": "",
        "channelTitle": "A trader",
        "thumbnails": {"default": {"url": f"https://i.ytimg.com/v{n}.jpg"}},
    }


class _Call:
    def __init__(self, answer):
        self._answer = answer

    def execute(self):
        return self._answer()


class FakeYouTube:
    """The Data API as the connector uses it, over a channel of `size` videos."""

    PAGE = 50

    def __init__(self, size: int):
        self.size = size
        self.deleted: set[int] = set()
        self.calls: list[tuple] = []

    def channels(self):
        return self

    def playlistItems(self):
        return _PlaylistItems(self)

    def videos(self):
        return _Videos(self)

    def list(self, **kw):                       # channels().list
        self.calls.append(("channels", kw.get("id")))
        return _Call(lambda: {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUchan"}}}]})


class _PlaylistItems:
    def __init__(self, api):
        self.api = api

    def list(self, *, playlistId, pageToken=None, **kw):
        api = self.api
        api.calls.append(("playlistItems", pageToken))
        start = int(pageToken or 0)
        live = [n for n in range(api.size) if n not in api.deleted]
        page = live[start:start + api.PAGE]
        answer = {"items": [
            {"snippet": {**_snippet(n), "resourceId": {"videoId": f"v{n}"}}, "contentDetails": {"videoId": f"v{n}"}}
            for n in page
        ]}
        if start + api.PAGE < len(live):
            answer["nextPageToken"] = str(start + api.PAGE)
        return _Call(lambda: answer)


class _Videos:
    def __init__(self, api):
        self.api = api

    def list(self, *, id, **kw):
        api = self.api
        api.calls.append(("videos", id))
        n = int(id.removeprefix("v"))
        found = n < api.size and n not in api.deleted
        return _Call(lambda: {"items": [{"id": id, "snippet": _snippet(n)}] if found else []})


@pytest.fixture
def youtube(monkeypatch):
    api = FakeYouTube(size=120)
    c = YouTubeConnector(source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(), channel_identifier="UCchan")

    async def _transcript(video_id, **fields):
        return [RawMessage(stable_id=f"youtube:{video_id}:full", text="transcript", content_type="video_transcript", **fields)]

    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(youtube_module, "_client", lambda api_key: api)
    monkeypatch.setattr(youtube_transcript, "video_transcript_messages", _transcript)
    return c, api


class TestAVideoIsFetchedAgainByItsOwnId:
    async def test_well_outside_the_newest_fifty(self, youtube):
        c, api = youtube

        msg = await c.fetch_one("youtube:v80:full")

        assert msg.stable_id == "youtube:v80:full"
        assert msg.metadata["title"] == "lesson 80"
        assert msg.timestamp == _published(80)

    async def test_for_one_quota_unit(self, youtube):
        c, api = youtube

        await c.fetch_one("youtube:v80:full")

        assert api.calls == [("videos", "v80")]

    async def test_a_whole_video_too(self, youtube):
        c, api = youtube

        [msg] = await c.fetch_again("youtube:v80")

        assert msg.stable_id == "youtube:v80:full"
        assert api.calls == [("videos", "v80")]

    async def test_one_deleted_or_made_private_is_gone(self, youtube):
        c, api = youtube
        api.deleted.add(80)

        assert await c.fetch_one("youtube:v80:full") is None
        assert await c.fetch_again("youtube:v80") is None


class TestABackfillReachesEveryVideoInItsDates:
    async def test_beyond_the_newest_fifty(self, youtube):
        c, api = youtube

        got = [m.stable_id async for m in c.fetch_range(_published(99), _published(60))]

        assert got == [f"youtube:v{n}:full" for n in range(99, 59, -1)]     # oldest first

    async def test_stops_reading_pages_once_past_its_start(self, youtube):
        c, api = youtube

        [_ async for _ in c.fetch_range(_published(70), None)]

        # Page 2 (videos 50–99) already reaches back past video 70.
        assert [call for call in api.calls if call[0] == "playlistItems"] == [
            ("playlistItems", None), ("playlistItems", "50"),
        ]

    async def test_a_range_reaching_back_past_the_channel_reads_it_all_once(self, youtube):
        c, api = youtube

        got = [m async for m in c.fetch_range(_published(500), None)]

        assert len(got) == 120
        assert len([call for call in api.calls if call[0] == "playlistItems"]) == 3


class TestThePollStillReadsTheNewestFifty:
    async def test_one_page_only(self, youtube):
        c, api = youtube

        got = [m async for m in c.fetch_new(FetchPosition())]

        assert len(got) == 50
        assert [call for call in api.calls if call[0] == "playlistItems"] == [("playlistItems", None)]
