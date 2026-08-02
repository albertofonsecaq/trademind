"""
Telethon-based Telegram connector.
Supports text, images, URLs, and video messages.
Content types are gated by content_filters so media is only downloaded when enabled.
"""
from __future__ import annotations
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument

from app.connectors.base import BaseConnector, RawMessage

_URL_RE = re.compile(r'https?://\S+')
_MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100 MB


class TelegramConnector(BaseConnector):
    def __init__(
        self,
        *,
        source_config_id: uuid.UUID,
        workspace_id: uuid.UUID,
        channel_identifier: str,
        api_id: int,
        api_hash: str,
        session_string: str,
        content_filters: dict | None = None,
    ):
        self.source_config_id = source_config_id
        self.workspace_id = workspace_id
        self.channel_identifier = channel_identifier
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        # Defaults: text=True, everything else=False (safe defaults)
        self.content_filters = content_filters or {"text": True}

    def _client(self) -> TelegramClient:
        return TelegramClient(StringSession(self.session_string), self.api_id, self.api_hash)

    def _want(self, key: str) -> bool:
        return bool(self.content_filters.get(key, False))

    def _author(self, msg: Message) -> str | None:
        if msg.sender:
            return getattr(msg.sender, "username", None) or getattr(msg.sender, "first_name", None)
        return None

    def _base_meta(self, msg: Message) -> dict:
        return {"message_id": msg.id, "views": getattr(msg, "views", None)}

    def _ts(self, msg: Message) -> datetime:
        return msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date

    # ── text ─────────────────────────────────────────────────────────────────

    def _text_raw(self, msg: Message) -> RawMessage | None:
        if not msg.text:
            return None
        return RawMessage(
            stable_id=f"telegram:{self.channel_identifier}:{msg.id}",
            source_config_id=self.source_config_id,
            workspace_id=self.workspace_id,
            text=msg.text,
            author=self._author(msg),
            channel=self.channel_identifier,
            timestamp=self._ts(msg),
            source_type="telegram",
            content_type="text",
            metadata=self._base_meta(msg),
        )

    # ── image ─────────────────────────────────────────────────────────────────

    async def _image_raw(self, client: TelegramClient, msg: Message) -> RawMessage | None:
        try:
            photo_bytes: bytes = await client.download_media(msg.photo, bytes)
        except Exception:
            return None
        if not photo_bytes:
            return None
        import base64
        return RawMessage(
            stable_id=f"telegram:{self.channel_identifier}:{msg.id}:image",
            source_config_id=self.source_config_id,
            workspace_id=self.workspace_id,
            text=msg.text or None,   # caption, if any
            author=self._author(msg),
            channel=self.channel_identifier,
            timestamp=self._ts(msg),
            source_type="telegram",
            content_type="image",
            metadata={
                **self._base_meta(msg),
                "image_base64": base64.b64encode(photo_bytes).decode(),
                "image_mime": "image/jpeg",
            },
        )

    # ── URL ──────────────────────────────────────────────────────────────────

    def _url_raws(self, msg: Message) -> list[RawMessage]:
        urls = set()
        # From Telethon entities
        if msg.entities:
            for ent in msg.entities:
                url = getattr(ent, "url", None)
                if url:
                    urls.add(url)
        # From raw text
        if msg.text:
            for u in _URL_RE.findall(msg.text):
                urls.add(u.rstrip(".,);"))

        result = []
        for i, url in enumerate(sorted(urls)):
            result.append(RawMessage(
                stable_id=f"telegram:{self.channel_identifier}:{msg.id}:url:{i}",
                source_config_id=self.source_config_id,
                workspace_id=self.workspace_id,
                text=None,  # filled after URL fetch
                author=self._author(msg),
                channel=self.channel_identifier,
                timestamp=self._ts(msg),
                source_type="telegram",
                content_type="url",
                metadata={**self._base_meta(msg), "url": url},
            ))
        return result

    # ── video ─────────────────────────────────────────────────────────────────

    async def _video_raw(self, client: TelegramClient, msg: Message) -> RawMessage | None:
        media = msg.video or msg.video_note or (
            msg.document if isinstance(msg.media, MessageMediaDocument)
            and msg.document and "video" in (getattr(msg.document, "mime_type", "") or "")
            else None
        )
        if not media:
            return None
        size = getattr(media, "size", None)
        if size and size > _MAX_VIDEO_BYTES:
            return None

        # Download to a named temp file (cleaned up by the pipeline after processing)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        try:
            await client.download_media(media, tmp.name)
        except Exception:
            os.unlink(tmp.name)
            return None

        return RawMessage(
            stable_id=f"telegram:{self.channel_identifier}:{msg.id}:video",
            source_config_id=self.source_config_id,
            workspace_id=self.workspace_id,
            text=msg.text or None,
            author=self._author(msg),
            channel=self.channel_identifier,
            timestamp=self._ts(msg),
            source_type="telegram",
            content_type="video",
            metadata={**self._base_meta(msg), "video_path": tmp.name},
        )

    # ── fetch helpers ─────────────────────────────────────────────────────────

    async def _yield_all_types(
        self, client: TelegramClient, msg: Message
    ) -> AsyncIterator[RawMessage]:
        if not isinstance(msg, Message):
            return

        # Text (default on)
        if self._want("text") and msg.text:
            raw = self._text_raw(msg)
            if raw:
                yield raw

        # Image
        if self._want("image") and isinstance(msg.media, MessageMediaPhoto):
            raw = await self._image_raw(client, msg)
            if raw:
                yield raw

        # URLs (only when there is no image — avoid tagging every URL in image captions)
        if self._want("url") and not isinstance(msg.media, MessageMediaPhoto):
            for raw in self._url_raws(msg):
                yield raw

        # Video
        if self._want("video"):
            has_video = (
                msg.video is not None
                or msg.video_note is not None
                or (
                    isinstance(msg.media, MessageMediaDocument)
                    and msg.document
                    and "video" in (getattr(msg.document, "mime_type", "") or "")
                )
            )
            if has_video:
                raw = await self._video_raw(client, msg)
                if raw:
                    yield raw

    # ── public interface ──────────────────────────────────────────────────────

    async def fetch_new(self, since_id: str | None) -> AsyncIterator[RawMessage]:
        min_id = 0
        if since_id:
            try:
                # stable_id may have suffixes (:image, :url:N, :video) — extract base msg id
                base = since_id.split(":")[2]
                min_id = int(base)
            except (ValueError, IndexError):
                min_id = 0

        async with self._client() as client:
            async for msg in client.iter_messages(
                self.channel_identifier, min_id=min_id, reverse=True
            ):
                async for raw in self._yield_all_types(client, msg):
                    yield raw

    async def fetch_range(
        self, date_start: datetime, date_end: datetime | None
    ) -> AsyncIterator[RawMessage]:
        async with self._client() as client:
            async for msg in client.iter_messages(
                self.channel_identifier, reverse=True, offset_date=date_start
            ):
                if not isinstance(msg, Message):
                    continue
                ts = self._ts(msg)
                if ts < date_start:
                    continue
                if date_end and ts > date_end:
                    break
                async for raw in self._yield_all_types(client, msg):
                    yield raw


# ── dialog listing ────────────────────────────────────────────────────────────

async def list_dialogs(*, api_id: int, api_hash: str, session_string: str) -> list[dict]:
    from telethon.tl.types import Channel, Chat
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    dialogs = []
    await client.connect()
    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, (Channel, Chat)):
                continue
            if isinstance(entity, Channel):
                dtype = "supergroup" if entity.megagroup else "channel"
                identifier = f"@{entity.username}" if getattr(entity, "username", None) else f"-100{entity.id}"
            else:
                dtype = "group"
                identifier = f"-{entity.id}"
            dialogs.append({"id": str(entity.id), "name": dialog.name, "type": dtype, "identifier": identifier})
    finally:
        await client.disconnect()
    return dialogs


# ── auth helpers ──────────────────────────────────────────────────────────────

async def start_telegram_auth(
    *, api_id: int, api_hash: str, phone: str
) -> tuple[str, str]:
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    session_string = client.session.save()
    await client.disconnect()
    return session_string, sent.phone_code_hash


async def complete_telegram_auth(
    *, api_id: int, api_hash: str, session_string: str, phone: str, code: str, phone_code_hash: str
) -> str:
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    final_session = client.session.save()
    await client.disconnect()
    return final_session
