"""
Video key-frame extraction via ffmpeg.
Extracts one frame per interval (default 30 s), up to max_frames.
Returns list of JPEG bytes — each frame is passed to vision_service independently.
Requires ffmpeg in PATH.
"""
from __future__ import annotations
import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100 MB hard limit
DEFAULT_INTERVAL_S = 30
DEFAULT_MAX_FRAMES = 10


async def extract_keyframes(
    video_path: str,
    interval_seconds: int = DEFAULT_INTERVAL_S,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> list[bytes]:
    """
    Extract key frames from a video file.
    Returns list of JPEG image bytes (empty list if ffmpeg not available or fails).
    """
    return await asyncio.get_event_loop().run_in_executor(
        None, _extract_sync, video_path, interval_seconds, max_frames
    )


def _extract_sync(
    video_path: str,
    interval_seconds: int,
    max_frames: int,
) -> list[bytes]:
    if not _ffmpeg_available():
        log.warning("ffmpeg not found in PATH — skipping key-frame extraction")
        return []

    file_size = os.path.getsize(video_path)
    if file_size > MAX_VIDEO_BYTES:
        log.warning(
            "Video file %.1f MB exceeds limit — skipping key-frame extraction",
            file_size / 1024 / 1024,
        )
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        output_pattern = os.path.join(tmpdir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps=1/{interval_seconds}",
            "-vframes", str(max_frames),
            "-q:v", "3",   # JPEG quality (2=best, 31=worst)
            output_pattern,
            "-y", "-loglevel", "error",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.warning("ffmpeg key-frame extraction failed: %s", e)
            return []

        frames = []
        for frame_file in sorted(Path(tmpdir).glob("frame_*.jpg")):
            frames.append(frame_file.read_bytes())
        return frames


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
