"""
Ingestion pipeline orchestrator.

Flow by content_type:
  text / video_transcript → relevance filter (Haiku) → distillation (Sonnet) → embed
  image                   → vision (Sonnet, combined extraction+relevance) → distillation → embed
  video                   → keyframe extraction → vision per frame → combine → distillation → embed
  url                     → fetch content → treat as text from here

All LLM/embedding/transcription calls log usage_events.
"""
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.models.evidence_item import EvidenceItem
from app.models.trade_idea import TradeIdea
from app.models.embedding_row import EmbeddingRow
from app.models.source_config import SourceConfig
from app.models.workspace import Workspace
from app.services import (
    relevance_service,
    distillation_service,
    embedding_service,
    vision_service,
    url_service,
    keyframe_service,
)
from app.services.usage_service import log_usage_event
from app.core.config import settings

log = logging.getLogger(__name__)

_TOKEN_COST = {
    settings.RELEVANCE_MODEL:    {"input": 0.00000025, "output": 0.00000125},
    settings.DISTILLATION_MODEL: {"input": 0.000003,   "output": 0.000015},
    settings.SYNTHESIS_MODEL:    {"input": 0.000003,   "output": 0.000015},
}


def _compute_cost(model: str, input_t: int, output_t: int) -> Decimal:
    rates = _TOKEN_COST.get(model, {"input": 0.0, "output": 0.0})
    return Decimal(str(rates["input"] * input_t + rates["output"] * output_t))


async def _is_duplicate(db: AsyncSession, workspace_id: uuid.UUID, stable_id: str) -> bool:
    result = await db.execute(
        select(EvidenceItem.id).where(
            EvidenceItem.workspace_id == workspace_id,
            EvidenceItem.stable_id == stable_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _run_in_executor(fn, *args):
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


def _detect_lang(text: str) -> str | None:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return None


# ── shared helpers ────────────────────────────────────────────────────────────

async def _distill_and_embed(
    db: AsyncSession,
    item: EvidenceItem,
    msg: RawMessage,
    text_for_embedding: str,
    lang: str | None,
) -> None:
    """Run distillation + embedding on an already-created, on-topic EvidenceItem."""
    fields, dist_usage = await distillation_service.distill(
        item.content, msg.author, msg.channel
    )

    idea = TradeIdea(
        evidence_item_id=item.id,
        workspace_id=msg.workspace_id,
        source_language=lang,
        symbol=fields.get("symbol"),
        setup_type=fields.get("setup_type"),
        action=fields.get("action"),
        entry=Decimal(str(fields["entry"])) if fields.get("entry") is not None else None,
        target=Decimal(str(fields["target"])) if fields.get("target") is not None else None,
        stop=Decimal(str(fields["stop"])) if fields.get("stop") is not None else None,
        summary_en=fields.get("summary_en"),
        summary_es=fields.get("summary_es"),
        original_text=item.content,
        author=msg.author,
        channel=msg.channel,
        message_timestamp=msg.timestamp,
    )
    db.add(idea)

    await log_usage_event(
        db,
        workspace_id=msg.workspace_id,
        task_type="distillation",
        provider_model=settings.DISTILLATION_MODEL,
        input_units=dist_usage["input_tokens"],
        output_units=dist_usage["output_tokens"],
        cost_usd=_compute_cost(settings.DISTILLATION_MODEL, dist_usage["input_tokens"], dist_usage["output_tokens"]),
    )

    embed_text = fields.get("summary_en") or text_for_embedding
    vector = await _run_in_executor(embedding_service.embed_passage, embed_text)

    emb = EmbeddingRow(
        workspace_id=msg.workspace_id,
        source_type=msg.source_type,
        source_id=msg.stable_id,
        text=embed_text,
        embedding=vector,
        source_language=lang,
        embedding_metadata={
            "author": msg.author,
            "channel": msg.channel,
            "symbol": fields.get("symbol"),
            "action": fields.get("action"),
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "stable_id": msg.stable_id,
            **(msg.metadata or {}),
        },
    )
    db.add(emb)

    await log_usage_event(
        db,
        workspace_id=msg.workspace_id,
        task_type="embedding",
        provider_model=settings.EMBEDDING_MODEL,
        input_units=len(embed_text.split()),
        output_units=0,
        cost_usd=Decimal("0"),
    )


# ── per-content-type processors ───────────────────────────────────────────────

async def _process_text(db: AsyncSession, msg: RawMessage, topic_scope: str) -> EvidenceItem | None:
    """Text and video_transcript content: standard relevance → distill → embed."""
    lang = _detect_lang(msg.text)

    item = EvidenceItem(
        workspace_id=msg.workspace_id,
        source_config_id=msg.source_config_id,
        stable_id=msg.stable_id,
        source_type=msg.source_type,
        type=msg.content_type,
        content=msg.text,
        confidence=1.0,
        original_language=lang,
        is_on_topic=None,
        source_metadata={"author": msg.author, "channel": msg.channel, **(msg.metadata or {})},
        message_timestamp=msg.timestamp,
    )
    db.add(item)
    await db.flush()

    on_topic, reason, rel_usage = await relevance_service.is_on_topic(msg.text, topic_scope)
    item.is_on_topic = on_topic
    item.relevance_reason = reason

    await log_usage_event(
        db,
        workspace_id=msg.workspace_id,
        task_type="relevance_filter",
        provider_model=settings.RELEVANCE_MODEL,
        input_units=rel_usage["input_tokens"],
        output_units=rel_usage["output_tokens"],
        cost_usd=_compute_cost(settings.RELEVANCE_MODEL, rel_usage["input_tokens"], rel_usage["output_tokens"]),
    )

    if not on_topic:
        return item

    await _distill_and_embed(db, item, msg, msg.text, lang)
    return item


async def _process_image(db: AsyncSession, msg: RawMessage, topic_scope: str) -> EvidenceItem | None:
    """Image: single vision call for extraction + relevance (no separate Haiku call)."""
    meta = msg.metadata or {}
    b64 = meta.get("image_base64")
    mime = meta.get("image_mime", "image/jpeg")

    if not b64:
        return None

    import base64
    image_bytes = base64.b64decode(b64)
    vision_result, vision_usage = await vision_service.extract_image(image_bytes, mime)

    is_on_topic = vision_result.get("is_on_topic", False)
    description = vision_result.get("description") or ""
    ocr_text = vision_result.get("ocr_text") or ""
    reason = vision_result.get("reason", "")
    confidence = float(vision_result.get("confidence", 1.0))

    # Combine description + OCR text as the evidence content
    content = "\n\n".join(filter(None, [description, ocr_text])) or "No trading content detected."

    item = EvidenceItem(
        workspace_id=msg.workspace_id,
        source_config_id=msg.source_config_id,
        stable_id=msg.stable_id,
        source_type=msg.source_type,
        type="image",
        content=content,
        confidence=confidence,
        original_language=_detect_lang(content) if content else None,
        is_on_topic=is_on_topic,
        relevance_reason=reason,
        source_metadata={"author": msg.author, "channel": msg.channel, **{k: v for k, v in meta.items() if k != "image_base64"}},
        message_timestamp=msg.timestamp,
    )
    db.add(item)
    await db.flush()

    await log_usage_event(
        db,
        workspace_id=msg.workspace_id,
        task_type="vision",
        provider_model=settings.DISTILLATION_MODEL,
        input_units=vision_usage["input_tokens"],
        output_units=vision_usage["output_tokens"],
        cost_usd=vision_service.vision_cost(vision_usage["input_tokens"], vision_usage["output_tokens"]),
    )

    if not is_on_topic:
        return item

    await _distill_and_embed(db, item, msg, content, item.original_language)
    return item


async def _process_url(db: AsyncSession, msg: RawMessage, topic_scope: str) -> EvidenceItem | None:
    """URL: fetch content, then run through text pipeline."""
    meta = msg.metadata or {}
    url = meta.get("url")
    if not url:
        return None

    fetched = await url_service.extract(url)
    if not fetched:
        return None

    text = fetched["text"]
    lang = _detect_lang(text)

    item = EvidenceItem(
        workspace_id=msg.workspace_id,
        source_config_id=msg.source_config_id,
        stable_id=msg.stable_id,
        source_type=msg.source_type,
        type="url",
        content=text,
        confidence=0.9,
        original_language=lang,
        is_on_topic=None,
        source_metadata={"author": msg.author, "channel": msg.channel, "url": url, "title": fetched.get("title"), **(msg.metadata or {})},
        message_timestamp=msg.timestamp,
    )
    db.add(item)
    await db.flush()

    on_topic, reason, rel_usage = await relevance_service.is_on_topic(text[:2000], topic_scope)
    item.is_on_topic = on_topic
    item.relevance_reason = reason

    await log_usage_event(
        db,
        workspace_id=msg.workspace_id,
        task_type="relevance_filter",
        provider_model=settings.RELEVANCE_MODEL,
        input_units=rel_usage["input_tokens"],
        output_units=rel_usage["output_tokens"],
        cost_usd=_compute_cost(settings.RELEVANCE_MODEL, rel_usage["input_tokens"], rel_usage["output_tokens"]),
    )

    if not on_topic:
        return item

    await _distill_and_embed(db, item, msg, text, lang)
    return item


async def _process_video(db: AsyncSession, msg: RawMessage, topic_scope: str) -> EvidenceItem | None:
    """
    Telegram video: extract key frames → Claude Vision per frame → combine on-topic frames.
    Temp video file is cleaned up after processing.
    """
    meta = msg.metadata or {}
    video_path = meta.get("video_path")

    try:
        if not video_path or not os.path.exists(video_path):
            return None

        frames = await keyframe_service.extract_keyframes(video_path)
        if not frames:
            return None

        descriptions: list[str] = []
        total_vision_input = 0
        total_vision_output = 0

        for frame_bytes in frames:
            result, usage = await vision_service.extract_image(frame_bytes)
            total_vision_input += usage["input_tokens"]
            total_vision_output += usage["output_tokens"]
            if result.get("is_on_topic") and result.get("description"):
                descriptions.append(result["description"])

        await log_usage_event(
            db,
            workspace_id=msg.workspace_id,
            task_type="vision",
            provider_model=settings.DISTILLATION_MODEL,
            input_units=total_vision_input,
            output_units=total_vision_output,
            cost_usd=vision_service.vision_cost(total_vision_input, total_vision_output),
        )

        is_on_topic = len(descriptions) > 0
        content = "\n\n".join(descriptions) if descriptions else "No trading content detected in video frames."
        lang = _detect_lang(content)

        item = EvidenceItem(
            workspace_id=msg.workspace_id,
            source_config_id=msg.source_config_id,
            stable_id=msg.stable_id,
            source_type=msg.source_type,
            type="video_transcript",
            content=content,
            confidence=0.8,
            original_language=lang,
            is_on_topic=is_on_topic,
            relevance_reason=f"{len(descriptions)}/{len(frames)} frames had trading content",
            source_metadata={"author": msg.author, "channel": msg.channel, **{k: v for k, v in meta.items() if k != "video_path"}},
            message_timestamp=msg.timestamp,
        )
        db.add(item)
        await db.flush()

        if is_on_topic:
            await _distill_and_embed(db, item, msg, content, lang)

        return item

    finally:
        # Always clean up the temp video file
        if video_path and os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except OSError:
                pass


# ── main entry point ──────────────────────────────────────────────────────────

async def process_message(
    db: AsyncSession,
    msg: RawMessage,
    topic_scope: str,
) -> EvidenceItem | None:
    """
    Route a raw message through the correct pipeline branch based on content_type.
    Returns the created EvidenceItem, or None if skipped.
    Does NOT commit — caller is responsible.
    """
    if await _is_duplicate(db, msg.workspace_id, msg.stable_id):
        log.debug("Skipping duplicate: %s", msg.stable_id)
        return None

    if not msg.text and msg.content_type in ("text", "video_transcript", "url"):
        return None

    # Log any pre-pipeline cost (e.g. Whisper in YouTube connector)
    pre_cost = Decimal(msg.pre_pipeline_cost_usd)
    if pre_cost > 0 and msg.pre_pipeline_task_type and msg.pre_pipeline_model:
        await log_usage_event(
            db,
            workspace_id=msg.workspace_id,
            task_type=msg.pre_pipeline_task_type,
            provider_model=msg.pre_pipeline_model,
            input_units=0,
            output_units=0,
            cost_usd=pre_cost,
        )

    ct = msg.content_type
    if ct in ("text", "video_transcript"):
        return await _process_text(db, msg, topic_scope)
    if ct == "image":
        return await _process_image(db, msg, topic_scope)
    if ct == "url":
        return await _process_url(db, msg, topic_scope)
    if ct == "video":
        return await _process_video(db, msg, topic_scope)

    log.warning("Unknown content_type '%s' for %s — skipping", ct, msg.stable_id)
    return None


# ── connector factory + run loop ──────────────────────────────────────────────

def _build_connector(source: SourceConfig):
    if source.source_type == "telegram":
        from app.connectors.telegram import TelegramConnector
        from app.core.encryption import decrypt_credential

        conn = source.connection
        if not conn or conn.status != "active":
            raise ValueError(f"Source {source.id} has no active Telegram connection")

        creds = conn.auth_metadata or {}
        session_str = decrypt_credential(conn.session_credential)
        return TelegramConnector(
            source_config_id=source.id,
            workspace_id=source.workspace_id,
            channel_identifier=source.identifier,
            api_id=int(creds.get("api_id", 0)),
            api_hash=creds.get("api_hash", ""),
            session_string=session_str,
            content_filters=source.content_filters,
        )

    if source.source_type == "youtube":
        from app.connectors.youtube import YouTubeConnector
        return YouTubeConnector(
            source_config_id=source.id,
            workspace_id=source.workspace_id,
            channel_identifier=source.identifier,
        )

    raise ValueError(f"Unknown source_type: {source.source_type}")


def _content_type_allowed(msg: RawMessage, filters: dict) -> bool:
    """Check whether the message content_type is enabled in the source's filters."""
    mapping = {"text": "text", "video_transcript": "video", "image": "image", "url": "url", "video": "video"}
    return filters.get(mapping.get(msg.content_type, "text"), True)


async def run_fetch_pipeline(
    db: AsyncSession,
    *,
    source: SourceConfig,
    workspace: Workspace,
) -> int:
    try:
        connector = _build_connector(source)
    except ValueError as e:
        log.warning("Cannot build connector for source %s: %s", source.id, e)
        return 0

    filters = source.content_filters or {}
    count = 0
    last_stable_id = source.last_fetched_id

    async for msg in connector.fetch_new(since_id=last_stable_id):
        if not _content_type_allowed(msg, filters):
            continue
        try:
            item = await process_message(db, msg, workspace.topic_scope)
            if item:
                count += 1
                last_stable_id = msg.stable_id
        except Exception as e:
            log.error("Pipeline error for %s: %s", msg.stable_id, e)
            await db.rollback()
            continue
        await db.commit()

    if last_stable_id != source.last_fetched_id:
        source.last_fetched_id = last_stable_id
        source.last_fetched_at = datetime.utcnow()
        await db.commit()

    return count
