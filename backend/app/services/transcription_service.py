"""
Whisper transcription fallback via OpenAI API.
Only called when youtube-transcript-api finds no captions for a video.
Requires OPENAI_API_KEY and yt-dlp (for audio extraction). When it gives
nothing it says why, as WhisperGaveNothing: the reason a person reads on the
video's failed message (#126).

The audio is downloaded from YouTube through the proxy when one is set, as the
captions are (#152, ADR 0014), and yt-dlp's words are scrubbed of it before
they reach a log line or a reason. The proxy itself failing is told apart
before they are, as DownloadProxyFailed (#153).
"""
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from decimal import Decimal

from app.core.config import settings
from app.services import youtube_pace, youtube_proxy, youtube_refusal
from app.services.youtube_cooldown import YouTubeRefused

log = logging.getLogger(__name__)

class WhisperGaveNothing(Exception):
    """Why Whisper produced no transcript: not configured, too long, or failed."""


class DownloadRefused(Exception):
    """YouTube refused the audio download: the same refusal as for captions (#130)."""


class DownloadProxyFailed(Exception):
    """
    The proxy in YOUTUBE_PROXY_URL failed the audio download (#153): why, in
    our own words, which name the setting and nothing of the URL.
    """


class _WarningsKept:
    """
    yt-dlp's logger: keeps what it warns of, says nothing.

    Through a broken proxy yt-dlp says so only in its warnings, and then fails
    as if its extractor were stale (#153). They are kept to be read, never
    logged: they name the proxy, credentials and all.
    """

    def __init__(self):
        self.kept: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.kept.append(message)

    def error(self, message: str) -> None:
        pass


# Whisper API pricing: $0.006 / minute (as of 2024)
WHISPER_COST_PER_MINUTE = Decimal("0.006")


async def transcribe_youtube_video(video_url: str) -> tuple[list[dict], Decimal] | None:
    """
    Download audio with yt-dlp, transcribe with OpenAI Whisper API.
    Returns (segments, cost_usd) where segments = [{text, start, duration}, ...].
    WhisperGaveNothing, saying why, if it is not configured or transcription fails;
    YouTubeRefused, with nothing downloaded, while YouTube's cool-down is in force.
    """
    if not settings.OPENAI_API_KEY:
        raise WhisperGaveNothing("Whisper is not configured (OPENAI_API_KEY is empty)")

    loop = asyncio.get_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # The download is a request to YouTube like the captions are
            # (#151): not made, nor waited for, while a refusal's cool-down is
            # in force — YouTubeRefused goes up as it is. The transcription
            # after it is OpenAI's, so it is made once the turn is given back:
            # caption requests do not wait behind it.
            async with youtube_pace.turn():
                audio_path = await loop.run_in_executor(None, _download_sync, video_url, tmpdir)
            return await loop.run_in_executor(None, _transcribe_sync, audio_path)
    except (WhisperGaveNothing, DownloadRefused, DownloadProxyFailed, YouTubeRefused):
        raise
    except Exception as e:
        # yt-dlp's words can name the proxy, credentials and all (#152).
        said = youtube_proxy.scrub(str(e))
        log.warning("Whisper transcription failed for %s: %s", video_url, said)
        raise WhisperGaveNothing(f"Whisper failed: {said}") from _cause(e)


def _cause(e: Exception) -> Exception | None:
    """
    What to chain an error passed on from yt-dlp to: the original, for its
    traceback, unless a proxy is set — then its words may name the proxy,
    credentials and all, and no traceback is to say them (#152).
    """
    return None if youtube_proxy.url() else e


def _download_audio(video_url: str, options: dict) -> None:
    """The video's audio, downloaded by yt-dlp with the `options` given."""
    import yt_dlp

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([video_url])


def _download_sync(video_url: str, tmpdir: str) -> str:
    """
    The path of the video's audio, an mp3 in `tmpdir`; DownloadRefused if
    YouTube refuses, DownloadProxyFailed if the proxy itself fails (#153).
    Through the proxy when one is set (#152).
    """
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
    warned = _WarningsKept()
    proxy = youtube_proxy.url()
    if proxy:
        ydl_opts["proxy"] = proxy
        ydl_opts["logger"] = warned

    try:
        _download_audio(video_url, ydl_opts)
    except Exception as e:
        # Only the download goes through YouTube; OpenAI's errors are not its
        # refusal. One met through the proxy is still a refusal (ADR 0009, 0014).
        if youtube_refusal.is_refusal(e):
            raise DownloadRefused(youtube_proxy.scrub(str(e))) from _cause(e)
        # Read here, with what yt-dlp warned of, before its words are
        # scrubbed and the proxy's failure can no longer be told apart (#153).
        proxy_failed = youtube_refusal.proxy_failed(e, warned.kept)
        if proxy_failed:
            raise DownloadProxyFailed(proxy_failed) from None
        raise

    mp3_files = [f for f in os.listdir(tmpdir) if f.endswith(".mp3")]
    if not mp3_files:
        raise RuntimeError("yt-dlp did not produce an mp3 file")
    return os.path.join(tmpdir, mp3_files[0])


def _ask_whisper(audio_path: str):
    """OpenAI's transcription of the audio at `audio_path`: its segments and duration."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        return client.audio.transcriptions.create(
            model=settings.WHISPER_MODEL,
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )


def _transcribe_sync(audio_path: str) -> tuple[list[dict], Decimal]:
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

    # OpenAI Whisper API has a 25 MB limit
    if file_size_mb > 25:
        raise WhisperGaveNothing(f"its audio is {file_size_mb:.1f} MB, over Whisper's 25 MB limit")

    response = _ask_whisper(audio_path)

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
