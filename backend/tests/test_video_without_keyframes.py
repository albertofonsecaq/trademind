"""
Tests for #95 — a video whose key frames could not be extracted vanished.

No frames meant no evidence: nothing was stored, nothing was flagged, and the
fetch position moved past the message. With ffmpeg missing from the machine
running the backend, 63 videos went that way on 25–26 Oct, each leaving only a
warning in the log.

A video with no frames to look at has not been judged, so it is kept as
evidence marked for reprocessing, with relevance unknown, not off-topic.

ffmpeg is made genuinely absent by pointing PATH at an empty directory.
"""
import uuid

import pytest

from app.connectors.base import RawMessage
from app.services import ingestion_pipeline, keyframe_service
from tests.conftest import FakeSession


@pytest.fixture
def no_ffmpeg(monkeypatch, tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _video(tmp_path) -> RawMessage:
    """A Telegram video as the connector hands it over: downloaded to a temp file."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not really a video")
    return RawMessage(
        stable_id="telegram:@chan:51",
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        text=None,
        author="someone",
        channel="@chan",
        timestamp=None,
        source_type="telegram",
        content_type="video",
        metadata={"message_id": 51, "video_path": str(path)},
    )


class TestAVideoWithNoKeyFrames:
    async def test_is_kept_and_flagged_for_reprocessing(self, offline_pipeline, no_ffmpeg, tmp_path):
        item = await ingestion_pipeline.process_message(FakeSession(), _video(tmp_path), "forex")

        assert item is not None
        assert item.type == "video_transcript"
        assert item.needs_reprocessing is True
        assert item.is_on_topic is None
        assert "ffmpeg" in item.relevance_reason
        assert item.source_metadata["message_id"] == 51
        assert "video_path" not in item.source_metadata

    async def test_says_so_when_ffmpeg_is_there_but_found_nothing(self, offline_pipeline, monkeypatch, tmp_path):
        async def _no_frames(video_path):
            return []

        monkeypatch.setattr(keyframe_service, "extract_keyframes", _no_frames)
        monkeypatch.setattr(keyframe_service, "ffmpeg_available", lambda: True)

        item = await ingestion_pipeline.process_message(FakeSession(), _video(tmp_path), "forex")

        assert item.needs_reprocessing is True
        assert item.is_on_topic is None
        assert "ffmpeg" not in item.relevance_reason
        assert "no key frames" in item.relevance_reason.lower()


class TestAMissingFfmpeg:
    """Reported once, when the backend starts, not once per video."""

    def test_is_reported_at_startup(self, no_ffmpeg, caplog):
        keyframe_service.warn_if_ffmpeg_missing()

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "ffmpeg" in warnings[0].getMessage()

    def test_is_not_reported_when_it_is_installed(self, monkeypatch, tmp_path, caplog):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "ffmpeg"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", str(bin_dir))

        keyframe_service.warn_if_ffmpeg_missing()

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    async def test_is_not_warned_about_again_for_each_video(self, no_ffmpeg, tmp_path, caplog):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"not really a video")

        frames = await keyframe_service.extract_keyframes(str(video))

        assert frames == []
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
