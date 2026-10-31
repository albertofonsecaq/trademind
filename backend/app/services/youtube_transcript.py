"""
One YouTube video → transcript messages, for whichever source found the video.

- youtube-transcript-api for captions (manual first, then generated, EN/ES preferred)
- OpenAI Whisper API fallback for videos without captions (optional)
- Long videos are chunked into ~YOUTUBE_CHUNK_SECONDS segments (default 5 min)
  → each chunk becomes its own evidence_item + embedding_row

Stable ids are `youtube:{video_id}:full` or `youtube:{video_id}:chunk_{n}`
whoever asks, so a video reached through a YouTube source and through a link
in a Telegram channel is recognised as the same content (#69, #70).
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from youtube_transcript_api import NoTranscriptFound, RequestBlocked, YouTubeTranscriptApi

from app.connectors.base import RawMessage
from app.core.clock import now_utc
from app.core.config import settings
from app.core.platform_refusal import PlatformRefused
from app.services import transcription_service
from app.services.usage_service import log_usage_event

log = logging.getLogger(__name__)


def stable_id_prefix(video_id: str) -> str:
    """What every stable id of this video's transcript starts with."""
    return f"youtube:{video_id}:"


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


class CaptionsRefused(PlatformRefused):
    """
    YouTube refused to hand over captions (a 429, or our address blocked).

    Not the same as a video without captions: every video after this one
    would be refused too, and it says nothing about the video (#124, #125).
    """


# Until when YouTube is not asked for captions, after it refused. In this
# process only: a restart asks once more, and a refusal then sets it again.
_refused_until: datetime | None = None


class NoTranscript(Exception):
    """
    The video has no captions and Whisper gave nothing: an ordinary failure,
    recorded and retried like any other (#126, ADR 0005), not a silent skip.
    """

    def __init__(self, video_id: str):
        whisper = (
            "Whisper produced nothing" if settings.OPENAI_API_KEY
            else "Whisper is not configured (OPENAI_API_KEY is empty)"
        )
        super().__init__(f"Video {video_id} has no captions, and {whisper}")


def _refusal(until: datetime) -> CaptionsRefused:
    return CaptionsRefused(f"YouTube is refusing caption requests until {until:%H:%M} UTC", until=until)


class _LibraryRefused(Exception):
    """The library's refusal, out of the executor thread."""


def _get_captions_sync(video_id: str) -> list[dict] | None:
    """The video's captions, or None if it has none; _LibraryRefused if YouTube refuses."""
    try:
        transcript_list = YouTubeTranscriptApi().list(video_id)
        # Prefer manually created, then auto-generated; prefer EN/ES
        try:
            t = transcript_list.find_manually_created_transcript(["en", "es"])
        except NoTranscriptFound:
            try:
                t = transcript_list.find_generated_transcript(["en", "es"])
            except NoTranscriptFound:
                # Fall back to any available transcript
                t = next(iter(transcript_list), None)
                if t is None:
                    return None
        return t.fetch().to_raw_data()
    except RequestBlocked as e:         # the library's IpBlocked, for a 429, is one
        raise _LibraryRefused(type(e).__name__) from e
    except Exception as e:
        log.debug("No captions for %s: %s", video_id, e)
        return None


async def fetch_captions(video_id: str) -> list[dict] | None:
    """
    The video's captions, or None if it has none.

    CaptionsRefused if YouTube refuses, and from then on without asking it,
    until the cool-down has passed: asking anyway is what got it to refuse
    (about 58,000 requests over three days, before #125).
    """
    global _refused_until
    now = now_utc()
    if _refused_until is not None and now < _refused_until:
        raise _refusal(_refused_until)
    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, _get_captions_sync, video_id
        )
    except _LibraryRefused as e:
        _refused_until = now + timedelta(minutes=settings.YOUTUBE_REFUSAL_COOLDOWN_MINUTES)
        log.warning(
            "YouTube refused captions for %s (%s): no caption requests until %s UTC",
            video_id, e, f"{_refused_until:%H:%M}",
        )
        raise _refusal(_refused_until) from e


async def _segments_for_video(video_id: str) -> tuple[list[dict], str]:
    """
    Return (segments, whisper_cost_usd_str).
    whisper_cost is "0" when captions were used (no cost).
    """
    # CaptionsRefused goes up as it is: the video is waited for, not paid for
    # with Whisper, which YouTube would likely refuse to download from too (#125).
    segments = await fetch_captions(video_id)
    if segments:
        return segments, "0"

    # Whisper fallback
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    result = await transcription_service.transcribe_youtube_video(video_url)
    if result:
        segs, cost = result
        return segs, str(cost)

    raise NoTranscript(video_id)


async def video_transcript_messages(
    video_id: str,
    *,
    source_config_id: uuid.UUID,
    workspace_id: uuid.UUID,
    author: str | None,
    channel: str,
    timestamp: datetime,
    source_type: str,
    metadata: dict | None = None,
) -> list[RawMessage]:
    """
    The video's transcript as `video_transcript` messages, one per chunk, or []
    when its transcript has no text. NoTranscript when it has neither captions
    nor a Whisper result (#126); CaptionsRefused when YouTube is refusing
    caption requests: the video is waited for (#125).

    The caller says whose evidence this is; `metadata` is merged under the
    chunk's own fields. Whisper cost rides on the first message only — the
    first one there is, since a chunk with no text is not one — and is
    recorded here when there are none, because nothing downstream would see it.
    """
    segments, owed = await _segments_for_video(video_id)
    chunks = _chunk_segments(
        segments,
        max_seconds=settings.YOUTUBE_CHUNK_SECONDS,
    )

    # Single-chunk videos don't need a numeric suffix in the stable_id
    use_suffix = len(chunks) > 1
    prefix = stable_id_prefix(video_id)
    messages: list[RawMessage] = []

    for i, chunk in enumerate(chunks):
        text = _chunk_to_text(chunk)
        if not text.strip():
            continue

        stable_id = f"{prefix}chunk_{i}" if use_suffix else f"{prefix}full"
        start_s = _chunk_start(chunk)
        end_s = _chunk_end(chunk)

        # Whisper cost is only charged once per video; the rest are free
        chunk_whisper_cost, owed = owed, "0"

        messages.append(RawMessage(
            stable_id=stable_id,
            source_config_id=source_config_id,
            workspace_id=workspace_id,
            text=text,
            author=author,
            channel=channel,
            timestamp=timestamp,
            source_type=source_type,
            content_type="video_transcript",
            metadata={
                **(metadata or {}),
                "video_id": video_id,
                "chunk_index": i,
                "chunk_start_seconds": start_s,
                "chunk_end_seconds": end_s,
                "video_url": f"https://www.youtube.com/watch?v={video_id}&t={int(start_s)}",
            },
            pre_pipeline_cost_usd=chunk_whisper_cost,
            pre_pipeline_task_type="transcription" if float(chunk_whisper_cost) > 0 else None,
            pre_pipeline_model=settings.WHISPER_MODEL if float(chunk_whisper_cost) > 0 else None,
        ))

    if Decimal(owed) > 0:
        await log_usage_event(
            workspace_id=workspace_id,
            task_type="transcription",
            provider_model=settings.WHISPER_MODEL,
            cost_usd=Decimal(owed),
        )
    return messages
