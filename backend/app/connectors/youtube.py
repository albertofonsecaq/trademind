"""
YouTube connector.
- Uses YouTube Data API v3 for video discovery (no per-workspace login needed)
- youtube-transcript-api for caption extraction
- OpenAI Whisper API fallback for videos without captions (optional)
- Long videos are chunked into ~YOUTUBE_CHUNK_SECONDS segments (default 5 min)
  → each chunk becomes its own evidence_item + embedding_row
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from app.connectors.base import BaseConnector, RawMessage
from app.core.config import settings

log = logging.getLogger(__name__)


def _chunk_segments(
    segments: list[dict],
    max_seconds: int = 300,
    max_words: int = 600,
) -> list[list[dict]]:
    """Split transcript segments into time-bounded chunks."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    cur_seconds = 0.0
    cur_words = 0

    for seg in segments:
        words = len(seg["text"].split())
        duration = float(seg.get("duration", 0))

        if current and (cur_seconds + duration > max_seconds or cur_words + words > max_words):
            chunks.append(current)
            current = []
            cur_seconds = 0.0
            cur_words = 0

        current.append(seg)
        cur_seconds += duration
        cur_words += words

    if current:
        chunks.append(current)

    return chunks


def _chunk_to_text(chunk: list[dict]) -> str:
    return " ".join(seg["text"].strip() for seg in chunk).strip()


def _chunk_start(chunk: list[dict]) -> float:
    return float(chunk[0].get("start", 0)) if chunk else 0.0


def _chunk_end(chunk: list[dict]) -> float:
    last = chunk[-1]
    return float(last.get("start", 0)) + float(last.get("duration", 0))


def _get_captions_sync(video_id: str) -> list[dict] | None:
    """Try to get captions. Returns None if unavailable."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prefer manually created, then auto-generated; prefer EN/ES
        try:
            t = transcript_list.find_manually_created_transcript(["en", "es"])
        except Exception:
            try:
                t = transcript_list.find_generated_transcript(["en", "es"])
            except Exception:
                # Fall back to any available transcript
                t = next(iter(transcript_list))
        return t.fetch()
    except Exception as e:
        log.debug("No captions for %s: %s", video_id, e)
        return None


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

    async def _get_captions(self, video_id: str) -> list[dict] | None:
        return await asyncio.get_event_loop().run_in_executor(
            None, _get_captions_sync, video_id
        )

    async def _segments_for_video(self, video: dict) -> tuple[list[dict] | None, str]:
        """
        Return (segments, whisper_cost_usd_str).
        whisper_cost is "0" when captions were used (no cost).
        """
        video_id = video["video_id"]
        segments = await self._get_captions(video_id)
        if segments:
            return segments, "0"

        # Whisper fallback
        from app.services.transcription_service import transcribe_youtube_video
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        result = await transcribe_youtube_video(video_url)
        if result:
            segs, cost = result
            return segs, str(cost)

        log.info(
            "No transcript available for %s (%s) — skipping",
            video["title"], video_id
        )
        return None, "0"

    def _parse_timestamp(self, published_at: str) -> datetime:
        try:
            return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    async def _yield_video_chunks(
        self, video: dict
    ) -> AsyncIterator[RawMessage]:
        video_id = video["video_id"]
        segments, whisper_cost = await self._segments_for_video(video)
        if not segments:
            return

        chunks = _chunk_segments(
            segments,
            max_seconds=settings.YOUTUBE_CHUNK_SECONDS,
        )

        # Single-chunk videos don't need a numeric suffix in the stable_id
        use_suffix = len(chunks) > 1
        published = self._parse_timestamp(video["published_at"])

        for i, chunk in enumerate(chunks):
            text = _chunk_to_text(chunk)
            if not text.strip():
                continue

            stable_id = f"youtube:{video_id}:chunk_{i}" if use_suffix else f"youtube:{video_id}:full"
            start_s = _chunk_start(chunk)
            end_s = _chunk_end(chunk)

            # Whisper cost is only charged once per video (first chunk); rest are free
            chunk_whisper_cost = whisper_cost if i == 0 else "0"

            yield RawMessage(
                stable_id=stable_id,
                source_config_id=self.source_config_id,
                workspace_id=self.workspace_id,
                text=text,
                author=video.get("channel_title"),
                channel=self.channel_identifier,
                timestamp=published,
                source_type="youtube",
                content_type="video_transcript",
                metadata={
                    "video_id": video_id,
                    "title": video["title"],
                    "chunk_index": i,
                    "chunk_start_seconds": start_s,
                    "chunk_end_seconds": end_s,
                    "video_url": f"https://www.youtube.com/watch?v={video_id}&t={int(start_s)}",
                    "thumbnail_url": video.get("thumbnail_url"),
                },
                pre_pipeline_cost_usd=chunk_whisper_cost,
                pre_pipeline_task_type="transcription" if float(chunk_whisper_cost) > 0 else None,
                pre_pipeline_model=settings.WHISPER_MODEL if float(chunk_whisper_cost) > 0 else None,
            )

    async def fetch_new(self, since_id: str | None) -> AsyncIterator[RawMessage]:
        """
        Fetch recent videos. Dedup is handled by the pipeline (stable_id check),
        so we simply yield everything and let it skip already-ingested items.
        since_id is used to skip videos published before the last processed one.
        """
        videos = await self._get_videos()

        # Extract the last-processed video_id from since_id ("youtube:{video_id}:...")
        last_video_id = None
        if since_id:
            parts = since_id.split(":")
            if len(parts) >= 2:
                last_video_id = parts[1]

        for video in reversed(videos):  # oldest-first
            if last_video_id and video["video_id"] == last_video_id:
                last_video_id = None  # found the marker — yield everything after
                continue
            if last_video_id:
                continue  # still before the marker

            async for msg in self._yield_video_chunks(video):
                yield msg

    async def fetch_range(
        self, date_start: datetime, date_end: datetime | None
    ) -> AsyncIterator[RawMessage]:
        """Yield chunks for videos published within [date_start, date_end]."""
        videos = await self._get_videos()

        for video in reversed(videos):  # oldest-first
            published = self._parse_timestamp(video["published_at"])
            if published < date_start:
                continue
            if date_end and published > date_end:
                continue

            async for msg in self._yield_video_chunks(video):
                yield msg
