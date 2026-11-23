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
import logging
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


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.fixture
def keyframe_log(caplog):
    """Everything keyframe_service logs, down to debug, so silence is not mistaken for none."""
    caplog.set_level(logging.DEBUG, logger=keyframe_service.log.name)
    return caplog


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


class TestAMissingFfmpeg:
    """Reported once, when the backend starts, not once per video."""

    def test_is_reported_at_startup(self, no_ffmpeg, keyframe_log):
        keyframe_service.warn_if_ffmpeg_missing()

        warnings = _warnings(keyframe_log)
        assert len(warnings) == 1
        assert "ffmpeg" in warnings[0].getMessage()

    def test_is_not_reported_when_it_is_installed(self, fake_ffmpeg, keyframe_log):
        keyframe_service.warn_if_ffmpeg_missing()

        assert not _warnings(keyframe_log)

    async def test_is_not_warned_about_again_for_each_video(self, no_ffmpeg, tmp_path, keyframe_log):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"not really a video")

        frames = await keyframe_service.extract_keyframes(str(video))

        assert frames == []
        assert not _warnings(keyframe_log)
        # Logged, just not as a warning: the capture is not what kept it quiet.
        assert any("ffmpeg" in r.getMessage() for r in keyframe_log.records)


class TestADownloadedVideo:
    """The connector downloads it; whatever the pipeline decides, the file goes (#97)."""

    async def test_already_stored_is_deleted_without_being_analysed(self, offline_pipeline, tmp_path, monkeypatch):
        from app.models.evidence_item import EvidenceItem

        async def _no_analysis(video_path):
            raise AssertionError("a duplicate must not be analysed")

        monkeypatch.setattr(keyframe_service, "extract_keyframes", _no_analysis)
        msg = _video(tmp_path)

        item = await ingestion_pipeline.process_message(
            FakeSession(rows={EvidenceItem: object()}), msg, "forex"
        )

        assert item is None
        assert not (tmp_path / "clip.mp4").exists()
