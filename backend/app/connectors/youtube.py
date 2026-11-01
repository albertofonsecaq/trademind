"""
YouTube connector.
- Uses YouTube Data API v3 for video discovery (no per-workspace login needed)
- Each video's transcript (captions, Whisper fallback, chunking) comes from
  app.services.youtube_transcript, which other sources share (#69)
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from typing import AsyncIterator

from app.connectors.base import BaseConnector, FetchPosition, RawMessage, to_aware_utc
from app.core.clock import now_utc
from app.core.config import settings
from app.core.content_unavailable import ContentUnavailable
from app.services import youtube_transcript

log = logging.getLogger(__name__)


def _client(api_key: str):
    """The YouTube Data API v3 client."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _uploads_playlist(youtube, channel_identifier: str) -> str:
    """
    The playlist of a channel's uploads. channel_identifier can be:
      - @handle (e.g. @MarketWizards)
      - UCxxxxx (channel ID)
      - PLxxxxx (playlist ID — used directly)
    """
    if channel_identifier.startswith("PL"):
        return channel_identifier
    channels = youtube.channels()
    if channel_identifier.startswith("UC"):
        items = channels.list(part="contentDetails", id=channel_identifier).execute().get("items", [])
    else:
        # Handle @username or plain username
        handle = channel_identifier.lstrip("@")
        items = channels.list(part="contentDetails", forHandle=handle).execute().get("items", [])
        if not items:
            # Try forUsername as fallback
            items = channels.list(part="contentDetails", forUsername=handle).execute().get("items", [])
    if not items:
        raise ValueError(f"Channel not found: {channel_identifier}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _video_record(video_id: str, snippet: dict, published_at: str) -> dict:
    """A video as this connector passes it around."""
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "published_at": published_at,
        "description": snippet.get("description", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
    }


def _parse_published(value: str) -> datetime | None:
    """An API timestamp ("…Z") as aware UTC (ADR 1), or None if it is not one."""
    try:
        return to_aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (AttributeError, ValueError):
        return None


_PAGE = 50          # the most the API hands back per page; each page is one quota unit


def _get_channel_videos_sync(
    channel_identifier: str, api_key: str, *, since: datetime | None = None
) -> list[dict]:
    """
    A channel's uploads, newest first, as video records.

    Without `since`, its newest page: what a poll needs. With it, page after
    page until one has nothing published since then, so a backfill gets every
    video in its dates rather than the newest 50 only (#131). A whole page, not
    its last video: the uploads are in the order they were added, and a
    premiere is added before it is published. A playlist source is in its
    owner's order, so it is read to its end.
    """
    youtube = _client(api_key)
    playlist_id = _uploads_playlist(youtube, channel_identifier)
    newest_first = not channel_identifier.startswith("PL")

    videos: list[dict] = []
    page_token: str | None = None
    while True:
        page = {"pageToken": page_token} if page_token else {}
        response = youtube.playlistItems().list(
            part="contentDetails,snippet", playlistId=playlist_id, maxResults=_PAGE, **page,
        ).execute()
        on_page = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            video_id = content.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            if video_id:
                # The snippet's date is when it was added to the playlist; the
                # video's own is in contentDetails (#131).
                published_at = content.get("videoPublishedAt") or snippet.get("publishedAt", "")
                on_page.append(_video_record(video_id, snippet, published_at))
        videos.extend(on_page)
        page_token = response.get("nextPageToken")
        if since is None or not page_token:
            return videos
        if newest_first and _nothing_since(on_page, since):
            return videos


def _nothing_since(videos: list[dict], since: datetime) -> bool:
    published = [_parse_published(v["published_at"]) for v in videos]
    return all(p is not None and p < since for p in published)


def _get_video_sync(video_id: str, api_key: str, channel_id: str | None) -> dict | None:
    """
    One video by its own id, wherever it is on its channel: one quota unit
    (#131). None when YouTube no longer shows it (deleted, or made private), or
    when it is another channel's. An API error raises: it is not "gone".
    """
    response = _client(api_key).videos().list(part="snippet", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        return None
    snippet = items[0].get("snippet", {})
    if channel_id and snippet.get("channelId") != channel_id:
        return None
    return _video_record(items[0]["id"], snippet, snippet.get("publishedAt", ""))


def whole_video_id(video_id: str | None) -> str:
    """The stable id of a video as a whole, which its transcript's chunk ids extend (#125)."""
    return f"youtube:{video_id}"


class YouTubeConnector(BaseConnector):
    def __init__(
        self,
        *,
        source_config_id: uuid.UUID,
        workspace_id: uuid.UUID,
        channel_identifier: str,
    ):
        self.source_config_id = source_config_id
        self.workspace_id = workspace_id
        self.channel_identifier = channel_identifier
        self._repaired: FetchPosition | None = None

    def _api_key(self) -> str:
        if not settings.YOUTUBE_API_KEY:
            raise RuntimeError("YOUTUBE_API_KEY is not configured")
        return settings.YOUTUBE_API_KEY

    async def _get_videos(self, since: datetime | None = None) -> list[dict]:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            partial(_get_channel_videos_sync, self.channel_identifier, self._api_key(), since=since),
        )

    async def _get_video(self, video_id: str | None) -> dict | None:
        if not video_id:
            return None
        # A channel given by its id is checked; a handle would cost a lookup
        # to resolve, and a playlist's videos are any channel's.
        channel_id = self.channel_identifier if self.channel_identifier.startswith("UC") else None
        return await asyncio.get_event_loop().run_in_executor(
            None, _get_video_sync, video_id, self._api_key(), channel_id,
        )

    def _parse_timestamp(self, published_at: str) -> datetime:
        # Aware UTC, which is what everything above the connectors uses (ADR 1);
        # a timestamp the API did not give falls back to now().
        return _parse_published(published_at) or now_utc()

    def _transcript_fields(self, video: dict) -> dict:
        """Whose a video's transcript is, and what it carries along: RawMessage's fields."""
        return dict(
            source_config_id=self.source_config_id,
            workspace_id=self.workspace_id,
            author=video.get("channel_title"),
            channel=self.channel_identifier,
            timestamp=self._parse_timestamp(video["published_at"]),
            source_type="youtube",
            metadata={"title": video["title"], "thumbnail_url": video.get("thumbnail_url")},
        )

    async def _transcript(self, video: dict) -> list[RawMessage]:
        """The video's transcript chunks; YouTubeRefused while YouTube refuses them."""
        return await youtube_transcript.video_transcript_messages(
            video["video_id"], **self._transcript_fields(video)
        )

    def _unfetched_video(self, video: dict, failure: ContentUnavailable) -> RawMessage:
        """
        A video whose transcript could not be had — YouTube refused its captions
        (#125), or it has none (#126) — handed on in its transcript's place: the
        pipeline records it as a failed message, and fetch_again hands back its
        transcript once there is one.
        """
        fields = self._transcript_fields(video)
        fields["metadata"] = {**fields["metadata"], "video_id": video["video_id"]}
        return RawMessage(
            stable_id=whole_video_id(video["video_id"]), text=None,
            content_type="youtube_video", fetch_failure=failure, **fields,
        )

    async def _yield_video_chunks(
        self, video: dict
    ) -> AsyncIterator[RawMessage]:
        try:
            messages = await self._transcript(video)
        except ContentUnavailable as failure:
            yield self._unfetched_video(video, failure)
            return
        for msg in messages:
            yield msg

    async def fetch_new(self, position: FetchPosition) -> AsyncIterator[RawMessage]:
        """
        Fetch recent videos, from the cursor forward.

        The cursor is a marker: the id of the last video processed, which has to
        be found in the channel's current window for "everything after it" to
        mean anything. Delete that video, make it private, or publish enough
        videos to push it out of the window, and the marker is unfindable — this
        used to skip the whole window and the source stopped ingesting silently,
        for good (#19).

        So a missing marker falls back to `since_timestamp`, the newest content
        already stored for this source. A date cannot be deleted, and because
        the bound is what we already hold, recovery pays for new videos only.
        With neither a marker nor a bound, the window is fetched and the
        pipeline's stable_id dedup absorbs anything already stored.
        """
        videos = await self._get_videos()
        marker = self._video_id_from(position.stable_id)
        bound: datetime | None = None
        self._repaired = None

        if marker and not any(v["video_id"] == marker for v in videos):
            bound = to_aware_utc(position.item_at)
            log.warning(
                "Position video %s is gone from %s's window; falling back to %s",
                marker, self.channel_identifier, bound or "the whole window",
            )
            marker = None
            # Whatever this run yields, the dead marker must not come back next
            # tick: the newest video in the window is where we have got to.
            if videos:
                newest = videos[0]
                self._repaired = FetchPosition(
                    stable_id=f"youtube:{newest['video_id']}:full",
                    item_at=self._parse_timestamp(newest["published_at"]),
                )

        for video in reversed(videos):                  # oldest first
            if marker is not None:
                # Still walking up to the marker; the marker video itself is
                # already stored, so it is skipped too.
                if video["video_id"] == marker:
                    marker = None
                continue
            if bound is not None and self._parse_timestamp(video["published_at"]) < bound:
                continue

            async for msg in self._yield_video_chunks(video):
                yield msg

    def repaired_position(self) -> FetchPosition | None:
        """
        Where the last fetch_new actually got to, when its own marker was dead.

        Strictly `<` above rather than `<=`: a video sharing the bound's exact
        second — YouTube publishes at second resolution, so batch uploads and
        premieres do — would otherwise be skipped by every future tick, silently
        and for ever. Re-offering that one second's videos costs at most a
        transcript the pipeline's stable_id dedup then discards, which is the
        cheaper mistake by far.
        """
        return self._repaired

    @staticmethod
    def _video_id_from(stable_id: str | None) -> str | None:
        """The video id inside a cursor ("youtube:{video_id}:...")."""
        if not stable_id:
            return None
        parts = stable_id.split(":")
        return parts[1] if len(parts) >= 2 else None

    async def fetch_one(self, stable_id: str) -> RawMessage | None:
        """
        One chunk of a video again, found by the video's own id wherever it is
        on its channel (#131). None only when YouTube no longer shows the video
        (deleted, or made private), which gives it up (ADR 0005). The whole
        video is transcribed again to hand back one chunk: free with captions,
        paid again without.
        """
        video_id = self._video_id_from(stable_id)
        video = await self._get_video(video_id)
        if video is None:
            return None
        found = None
        paid: RawMessage | None = None
        # A refusal or a missing transcript goes up as it is: read as gone, the
        # chunk would be given up at once (#125, #126).
        for msg in await self._transcript(video):
            if msg.stable_id == stable_id:
                found = msg
            if Decimal(msg.pre_pipeline_cost_usd) > 0:
                paid = msg
        if found is None or paid is None or paid is found:
            return found
        # The transcription that produced it rides on the first chunk; asking for
        # a later one paid it all the same, so it is logged with this one (#27).
        return replace(
            found,
            pre_pipeline_cost_usd=paid.pre_pipeline_cost_usd,
            pre_pipeline_task_type=paid.pre_pipeline_task_type,
            pre_pipeline_model=paid.pre_pipeline_model,
        )

    async def fetch_again(self, stable_id: str) -> list[RawMessage] | None:
        """
        A failed message again. A video that waited out a refusal comes back as
        its whole transcript, chunked; a chunk, as fetch_one has it (#125).
        """
        if stable_id != whole_video_id(self._video_id_from(stable_id)):
            return await super().fetch_again(stable_id)
        video_id = self._video_id_from(stable_id)
        video = await self._get_video(video_id)
        return None if video is None else await self._transcript(video)

    async def latest_stable_id(self) -> str | None:
        """
        Newest video, no transcript work: fetch_new only reads the video id out
        of the cursor, so the suffix here just has to be a shape it recognizes.
        """
        videos = await self._get_videos()
        if not videos:
            return None
        return f"youtube:{videos[0]['video_id']}:full"

    async def fetch_range(
        self, date_start: datetime, date_end: datetime | None
    ) -> AsyncIterator[RawMessage]:
        """Yield chunks for videos published within [date_start, date_end], however far back (#131)."""
        date_start = to_aware_utc(date_start)
        date_end = to_aware_utc(date_end)
        videos = await self._get_videos(since=date_start)

        for video in reversed(videos):  # oldest-first
            published = self._parse_timestamp(video["published_at"])
            if published < date_start:
                continue
            if date_end and published > date_end:
                continue

            async for msg in self._yield_video_chunks(video):
                yield msg
