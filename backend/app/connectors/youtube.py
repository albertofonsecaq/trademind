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
from typing import AsyncIterator

from app.connectors.base import BaseConnector, FetchPosition, RawMessage, to_aware_utc
from app.core.clock import now_utc
from app.core.config import settings
from app.services import youtube_transcript

log = logging.getLogger(__name__)


def _get_channel_videos_sync(
    channel_identifier: str, api_key: str, max_results: int = 50
) -> list[dict]:
    """
    Fetch recent videos from a channel via YouTube Data API v3.
    Returns list of {video_id, title, published_at, description, channel_title, thumbnail_url}.
    channel_identifier can be:
      - @handle (e.g. @MarketWizards)
      - UCxxxxx (channel ID)
      - PLxxxxx (playlist ID — used directly)
    """
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    # Step 1: Resolve to uploads playlist ID
    if channel_identifier.startswith("PL"):
        playlist_id = channel_identifier
    elif channel_identifier.startswith("UC"):
        resp = youtube.channels().list(
            part="contentDetails", id=channel_identifier
        ).execute()
        items = resp.get("items", [])
        if not items:
            raise ValueError(f"Channel not found: {channel_identifier}")
        playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    else:
        # Handle @username or plain username
        handle = channel_identifier.lstrip("@")
        resp = youtube.channels().list(
            part="contentDetails", forHandle=handle
        ).execute()
        items = resp.get("items", [])
        if not items:
            # Try forUsername as fallback
            resp = youtube.channels().list(
                part="contentDetails", forUsername=handle
            ).execute()
            items = resp.get("items", [])
        if not items:
            raise ValueError(f"Channel not found: {channel_identifier}")
        playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Step 2: Get recent videos from uploads playlist
    videos = []
    request = youtube.playlistItems().list(
        part="contentDetails,snippet",
        playlistId=playlist_id,
        maxResults=min(max_results, 50),
    )
    response = request.execute()
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        video_id = content.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        if not video_id:
            continue
        videos.append({
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "published_at": snippet.get("publishedAt", ""),
            "description": snippet.get("description", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
        })

    return videos


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

    async def _get_videos(self) -> list[dict]:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            _get_channel_videos_sync,
            self.channel_identifier,
            self._api_key(),
        )

    def _parse_timestamp(self, published_at: str) -> datetime:
        # Aware UTC, which is what everything above the connectors uses (ADR 1).
        # The API returns "…Z"; the fallback is now(), so both go through one path.
        try:
            return to_aware_utc(datetime.fromisoformat(published_at.replace("Z", "+00:00")))
        except Exception:
            return now_utc()

    def _credit(self, video: dict) -> dict:
        """Whose a video's transcript is, and what it carries along."""
        return dict(
            source_config_id=self.source_config_id,
            workspace_id=self.workspace_id,
            author=video.get("channel_title"),
            channel=self.channel_identifier,
            timestamp=self._parse_timestamp(video["published_at"]),
            source_type="youtube",
            metadata={"title": video["title"], "thumbnail_url": video.get("thumbnail_url")},
        )

    def _the_video(self, video: dict) -> RawMessage:
        """
        The video itself, for the pipeline to ask its transcript for: what a
        refused video becomes, so the refusal is recorded against it and waited
        out instead of the video being lost (#125, ADR 0007).
        """
        credit = self._credit(video)
        credit["metadata"] = {**credit["metadata"], "video_id": video["video_id"]}
        return RawMessage(
            stable_id=f"youtube:{video['video_id']}", text=None, content_type="youtube_video", **credit
        )

    async def _yield_video_chunks(
        self, video: dict
    ) -> AsyncIterator[RawMessage]:
        try:
            messages = await youtube_transcript.video_transcript_messages(
                video["video_id"], **self._credit(video)
            )
        except youtube_transcript.CaptionsRefused:
            yield self._the_video(video)
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
        One chunk of a video again, found through the channel's current window.

        The window is the only way this connector finds a video, so one that has
        left it can no longer be fetched and comes back as None (ADR 0005). The
        whole video is transcribed again to hand back one chunk: free with
        captions, paid again without.
        """
        video_id = self._video_id_from(stable_id)
        video = next((v for v in await self._get_videos() if v["video_id"] == video_id), None)
        if video is None:
            return None
        if stable_id == f"youtube:{video_id}":
            # A refused video: the pipeline asks for its transcript (#125).
            return self._the_video(video)
        found = None
        paid: RawMessage | None = None
        async for msg in self._yield_video_chunks(video):
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
        """Yield chunks for videos published within [date_start, date_end]."""
        date_start = to_aware_utc(date_start)
        date_end = to_aware_utc(date_end)
        videos = await self._get_videos()

        for video in reversed(videos):  # oldest-first
            published = self._parse_timestamp(video["published_at"])
            if published < date_start:
                continue
            if date_end and published > date_end:
                continue

            async for msg in self._yield_video_chunks(video):
                yield msg
