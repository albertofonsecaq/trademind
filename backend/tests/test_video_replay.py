"""
Tests for #97 — a Telegram video flagged for reprocessing is replayed in place.

Since #95 a video with no key frames is kept, marked for reprocessing, instead
of being dropped. The targeted replay already re-ran vision over flagged images
by walking the source again and updating each row; it now does the same for
flagged Telegram videos — which the connector downloads to a temp file, so
every video it hands over has to be deleted again, matched or not.

A flagged video is told apart from a YouTube transcript, which has the same
evidence type, by its source.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.connectors.base import RawMessage
from app.models.evidence_item import EvidenceItem
from app.services import ingestion_pipeline, keyframe_service, reprocess_service, vision_service
from tests.conftest import make_source

_AT = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


class FakeChannel:
    """The source as the replay walks it: whatever messages are put in it."""

    def __init__(self):
        self.messages: list[RawMessage] = []
        self.ranges: list[tuple] = []

    async def fetch_range(self, date_start, date_end):
        self.ranges.append((date_start, date_end))
        for msg in self.messages:
            yield msg


@pytest.fixture
def channel(monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr(ingestion_pipeline, "_build_connector", lambda source: channel)
    return channel


@pytest.fixture
def frames(monkeypatch, offline_pipeline):
    """Key frames and vision, answered locally: frames per video path, and every call made."""
    state = {"frames": {}, "vision_calls": 0, "distilled": []}

    async def _extract(video_path):
        return state["frames"].get(os.path.basename(video_path), [])

    async def _vision(image_bytes, mime="image/jpeg"):
        state["vision_calls"] += 1
        return (
            {"is_on_topic": True, "description": "EURUSD long above the range", "needs_reprocessing": False},
            {"input_tokens": 100, "output_tokens": 20},
        )

    async def _distill(db, item, msg, text, lang):
        state["distilled"].append(item.stable_id)

    monkeypatch.setattr(keyframe_service, "extract_keyframes", _extract)
    monkeypatch.setattr(keyframe_service, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(vision_service, "extract_image", _vision)
    monkeypatch.setattr(ingestion_pipeline, "_distill_and_embed", _distill)
    return state


def _downloaded(tmp_path, source, msg_id: int) -> RawMessage:
    """A video as the connector hands it over: already downloaded to a temp file."""
    path = tmp_path / f"video-{msg_id}.mp4"
    path.write_bytes(b"not really a video")
    return RawMessage(
        stable_id=f"telegram:chan:{msg_id}:video",
        source_config_id=source.id,
        workspace_id=source.workspace_id,
        text="watch this",
        author="someone",
        channel="chan",
        timestamp=_AT,
        source_type="telegram",
        content_type="video",
        metadata={"message_id": msg_id, "video_path": str(path)},
    )


async def _flagged_video(db, source, msg_id: int, *, source_type="telegram") -> EvidenceItem:
    item = EvidenceItem(
        workspace_id=source.workspace_id,
        source_config_id=source.id,
        stable_id=f"{source_type}:chan:{msg_id}:video",
        source_type=source_type,
        type="video_transcript",
        content="Video not analysed: no key frames could be extracted.",
        confidence=0.0,
        is_on_topic=None,
        needs_reprocessing=True,
        relevance_reason="No key frames extracted: ffmpeg is not installed",
        source_metadata={"author": "someone", "channel": "chan", "message_id": msg_id},
        message_timestamp=_AT,
    )
    db.add(item)
    await db.flush()
    return item


async def _reread(db, item) -> EvidenceItem:
    db.expunge(item)
    return (await db.execute(select(EvidenceItem).where(EvidenceItem.id == item.id))).scalar_one()


@pytest.mark.db
class TestAFlaggedTelegramVideo:
    async def test_is_analysed_and_updated_in_place(self, db_session, channel, frames, tmp_path):
        source = await make_source(db_session)
        item = await _flagged_video(db_session, source, 51)
        msg = _downloaded(tmp_path, source, 51)
        channel.messages.append(msg)
        frames["frames"]["video-51.mp4"] = [b"frame-1", b"frame-2"]

        report = await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        replayed = await _reread(db_session, item)
        assert replayed.content == "EURUSD long above the range\n\nEURUSD long above the range"
        assert replayed.is_on_topic is True
        assert replayed.needs_reprocessing is False
        assert replayed.relevance_reason == "2/2 frames had trading content"
        assert frames["distilled"] == [item.stable_id]
        assert (report.flagged, report.matched, report.recovered_on_topic) == (1, 1, 1)
        assert not os.path.exists(msg.metadata["video_path"])

    async def test_that_still_has_no_frames_stays_flagged(self, db_session, channel, frames, tmp_path):
        source = await make_source(db_session)
        item = await _flagged_video(db_session, source, 51)
        channel.messages.append(_downloaded(tmp_path, source, 51))

        report = await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        replayed = await _reread(db_session, item)
        assert replayed.needs_reprocessing is True
        assert replayed.is_on_topic is None
        assert report.still_unreadable == 1
        assert frames["distilled"] == []

    async def test_is_only_found_in_a_dry_run(self, db_session, channel, frames, tmp_path):
        source = await make_source(db_session)
        item = await _flagged_video(db_session, source, 51)
        msg = _downloaded(tmp_path, source, 51)
        channel.messages.append(msg)
        frames["frames"]["video-51.mp4"] = [b"frame-1"]

        report = await reprocess_service.reprocess_flagged(
            db_session, source_config_id=source.id, dry_run=True
        )

        assert report.matched == 1
        assert frames["vision_calls"] == 0
        assert (await _reread(db_session, item)).needs_reprocessing is True
        assert not os.path.exists(msg.metadata["video_path"])


@pytest.mark.db
class TestTheReplayScan:
    async def test_deletes_every_video_it_downloads_not_only_the_flagged_ones(
        self, db_session, channel, frames, tmp_path
    ):
        source = await make_source(db_session)
        await _flagged_video(db_session, source, 51)
        other = _downloaded(tmp_path, source, 50)
        channel.messages += [other, _downloaded(tmp_path, source, 51)]

        await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        assert not os.path.exists(other.metadata["video_path"])

    async def test_leaves_a_youtube_transcript_alone(self, db_session, channel, frames):
        source = await make_source(db_session)
        await _flagged_video(db_session, source, 51, source_type="youtube")

        report = await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        assert report.flagged == 0
        assert channel.ranges == []


@pytest.mark.db
class TestAFlaggedImage:
    """The replay this grew from (#3), unchanged by taking videos too."""

    async def test_is_still_analysed_and_updated_in_place(self, db_session, channel, frames):
        import base64

        source = await make_source(db_session)
        item = EvidenceItem(
            workspace_id=source.workspace_id, source_config_id=source.id,
            stable_id="telegram:chan:60:image", source_type="telegram", type="image",
            content="unreadable", confidence=0.0, is_on_topic=None, needs_reprocessing=True,
            message_timestamp=_AT,
        )
        db_session.add(item)
        await db_session.flush()
        channel.messages.append(RawMessage(
            stable_id="telegram:chan:60:image", source_config_id=source.id,
            workspace_id=source.workspace_id, text=None, author="someone", channel="chan",
            timestamp=_AT, source_type="telegram", content_type="image",
            metadata={"message_id": 60, "image_base64": base64.b64encode(b"jpeg").decode()},
        ))

        report = await reprocess_service.reprocess_flagged(db_session, source_config_id=source.id)

        replayed = await _reread(db_session, item)
        assert replayed.content == "EURUSD long above the range"
        assert replayed.needs_reprocessing is False
        assert report.recovered_on_topic == 1
        assert frames["distilled"] == ["telegram:chan:60:image"]
