"""
Whisper transcription fallback via OpenAI API.
Only called when youtube-transcript-api finds no captions for a video.
Requires OPENAI_API_KEY and yt-dlp (for audio extraction).
If either is missing, the video is skipped — never a silent error.
"""
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from decimal import Decimal

from app.core.config import settings

log = logging.getLogger(__name__)

# Whisper API pricing: $0.006 / minute (as of 2024)
WHISPER_COST_PER_MINUTE = Decimal("0.006")


async def transcribe_youtube_video(video_url: str) -> tuple[list[dict], Decimal] | None:
    """
    Download audio with yt-dlp, transcribe with OpenAI Whisper API.
    Returns (segments, cost_usd) where segments = [{text, start, duration}, ...].
    Returns None if Whisper is not configured or transcription fails.
    """
    if not settings.OPENAI_API_KEY:
        log.debug("OPENAI_API_KEY not set — skipping Whisper transcription for %s", video_url)
        return None

    try:
        return await asyncio.get_event_loop().run_in_executor(
            None, _transcribe_sync, video_url
        )
    except Exception as e:
        log.warning("Whisper transcription failed for %s: %s", video_url, e)
        return None


def _transcribe_sync(video_url: str) -> tuple[list[dict], Decimal] | None:
    import yt_dlp
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",  # low quality enough for speech
            }],
            "quiet": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        # Find the downloaded mp3
        mp3_files = [f for f in os.listdir(tmpdir) if f.endswith(".mp3")]
        if not mp3_files:
            raise RuntimeError("yt-dlp did not produce an mp3 file")

        audio_path = os.path.join(tmpdir, mp3_files[0])
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        # OpenAI Whisper API has a 25 MB limit
        if file_size_mb > 25:
            log.warning("Audio file %.1f MB exceeds Whisper 25 MB limit — skipping", file_size_mb)
            return None

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=settings.WHISPER_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments = []
        for seg in (response.segments or []):
            segments.append({
                "text": seg.text.strip(),
                "start": float(seg.start),
                "duration": float(seg.end) - float(seg.start),
            })

        # Approximate cost: duration_minutes * rate
        duration_minutes = Decimal(str(response.duration / 60)) if response.duration else Decimal("0")
        cost = duration_minutes * WHISPER_COST_PER_MINUTE

        return segments, cost
