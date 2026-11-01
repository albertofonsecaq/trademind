"""
Tests for #96 — a connector fetches one message again by its stable id.

That is how a failed message is retried (ADR 0005): the platform is asked for
that message as it is now, not for a copy kept from the failed attempt. A stable
id names one part of a message — its text, its image, its Nth link — so the
part asked for is the one handed back. A message the platform no longer has
comes back as None, which gives it up.
"""
import uuid
from datetime import datetime, timezone

import pytest
from telethon.tl.types import Message, PeerChannel

from app.connectors.base import RawMessage
from app.connectors.telegram import TelegramConnector
from app.connectors.youtube import YouTubeConnector

_NOON = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class _FakeTelegramClient:
    """Answers get_messages by id, from what the channel still holds."""

    def __init__(self, held: dict[int, Message]):
        self._held = held

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_messages(self, entity, ids):
        return self._held.get(ids)


def _message(msg_id: int, text: str) -> Message:
    msg = Message(id=msg_id, peer_id=PeerChannel(123), date=_NOON, message=text)
    msg._text = msg.message      # a detached Message resolves .text lazily via a client
    return msg


@pytest.fixture
def telegram():
    c = TelegramConnector(
        source_config_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        channel_identifier="123/456",
        api_id=1,
        api_hash="x",
        session_string="x",
        content_filters={"text": True, "url": True},
    )
    held = {42: _message(42, "EURUSD long, details at https://example.com/ecb")}
    c._resolve_identifier = lambda: "entity"
    c._client = lambda: _FakeTelegramClient(held)
    return c


class TestTelegramFetchesOneMessageAgain:
    async def test_hands_back_the_part_asked_for(self, telegram):
        msg = await telegram.fetch_one("telegram:123/456:42:url:0")

        assert msg.stable_id == "telegram:123/456:42:url:0"
        assert msg.content_type == "url"
        assert msg.metadata["url"] == "https://example.com/ecb"

    async def test_hands_back_the_text_when_that_is_what_failed(self, telegram):
        msg = await telegram.fetch_one("telegram:123/456:42")

        assert msg.content_type == "text"
        assert msg.timestamp == _NOON

    async def test_hands_back_none_for_a_message_the_channel_no_longer_has(self, telegram):
        assert await telegram.fetch_one("telegram:123/456:43") is None


# ── YouTube ──────────────────────────────────────────────────────────────────

WINDOW = [
    {"video_id": "v2", "title": "v2", "published_at": "2026-10-15T09:00:00Z"},
    {"video_id": "v1", "title": "v1", "published_at": "2026-10-10T09:00:00Z"},
]


@pytest.fixture
def youtube(monkeypatch):
    c = YouTubeConnector(source_config_id=uuid.uuid4(), workspace_id=uuid.uuid4(), channel_identifier="@chan")

    async def _videos():
        return WINDOW

    async def _chunks(video):
        return [_chunk(video, n) for n in range(3)]

    def _chunk(video, n):
        # As video_transcript_messages does: Whisper's cost rides on the first chunk.
        cost = "0.42" if n == 0 else "0"
        return RawMessage(
            pre_pipeline_cost_usd=cost,
            pre_pipeline_task_type="transcription" if n == 0 else None,
            pre_pipeline_model="whisper" if n == 0 else None,
            stable_id=f"youtube:{video['video_id']}:chunk_{n}",
            source_config_id=c.source_config_id,
            workspace_id=c.workspace_id,
            text=f"chunk {n}",
            author=None,
            channel="@chan",
            timestamp=c._parse_timestamp(video["published_at"]),
            source_type="youtube",
            content_type="video_transcript",
        )

    async def _video(video_id):
        return next((v for v in WINDOW if v["video_id"] == video_id), None)

    monkeypatch.setattr(c, "_get_videos", _videos)
    monkeypatch.setattr(c, "_get_video", _video)
    monkeypatch.setattr(c, "_transcript", _chunks)
    return c


class TestYouTubeFetchesOneChunkAgain:
    async def test_hands_back_the_chunk_asked_for(self, youtube):
        msg = await youtube.fetch_one("youtube:v1:chunk_1")

        assert msg.stable_id == "youtube:v1:chunk_1"
        assert msg.text == "chunk 1"

    async def test_carries_the_transcription_paid_again_to_get_it(self, youtube):
        """Retrying chunk 1 transcribes the whole video again; that cost is logged (#27)."""
        msg = await youtube.fetch_one("youtube:v1:chunk_1")

        assert (msg.pre_pipeline_cost_usd, msg.pre_pipeline_task_type, msg.pre_pipeline_model) == (
            "0.42", "transcription", "whisper",
        )

    async def test_hands_back_none_for_a_video_gone_from_the_window(self, youtube):
        assert await youtube.fetch_one("youtube:v0:chunk_1") is None
