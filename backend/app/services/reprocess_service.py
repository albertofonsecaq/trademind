"""
Targeted replay for evidence items flagged needs_reprocessing: images whose
vision reply was unreadable (#3), and Telegram videos kept without key frames
(#95, #97).

Why this exists rather than a backfill: run_fetch_pipeline INSERTs, so replaying
a message that is already stored raises IntegrityError on uq_evidence_stable_id
and is skipped — after the vision call has already been paid for. This fetches
each flagged message again from the platform by its stable id and UPDATEs its
row in place.

It used to walk the source over the flagged messages' date range instead, which
for five images spread over five years meant downloading every photo and video
of ~25,000 messages to find them (#97).

Dry-run fetches each one without a single model call, so recoverability can be
checked for free before committing to the spend.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.llm_errors import is_fatal_for_run
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.models.trade_idea import TradeIdea
from app.services import vision_service
from app.services.connection_health import credentials_rejected
from app.services.usage_service import log_usage_event

log = logging.getLogger(__name__)

# What the replay knows how to redo. A YouTube transcript has the same evidence
# type as a Telegram video, and is told apart by where it came from.
REPLAYABLE = or_(
    EvidenceItem.type == "image",
    and_(EvidenceItem.type == "video_transcript", EvidenceItem.source_type == "telegram"),
)


@dataclass
class ReprocessReport:
    flagged: int = 0            # items asked for
    matched: int = 0            # flagged messages the source still has
    recovered_on_topic: int = 0
    recovered_off_topic: int = 0
    still_unreadable: int = 0   # vision failed again — stays flagged
    errors: int = 0
    # The source no longer offers them: deleted, or their kind now filtered out.
    unreachable: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        head = "DRY RUN — no model calls made" if self.dry_run else "reprocessed"
        return (
            f"{head}: flagged={self.flagged} matched={self.matched} "
            f"on_topic={self.recovered_on_topic} off_topic={self.recovered_off_topic} "
            f"still_unreadable={self.still_unreadable} errors={self.errors} "
            f"unreachable={len(self.unreachable)}"
        )


async def reprocess_flagged(
    db: AsyncSession,
    *,
    source_config_id: uuid.UUID,
    limit: int | None = None,
    dry_run: bool = False,
) -> ReprocessReport:
    """Replay the flagged images and videos of one source and update them in place."""
    from app.services import ingestion_pipeline

    report = ReprocessReport(dry_run=dry_run)

    pending = await _load_flagged(db, source_config_id, limit)
    report.flagged = len(pending)
    if not pending:
        return report

    src_result = await db.execute(
        select(SourceConfig)
        .options(selectinload(SourceConfig.connection))
        .where(SourceConfig.id == source_config_id)
    )
    source = src_result.scalar_one_or_none()
    if source is None:
        raise ValueError(f"Source {source_config_id} not found")

    workspace_id = source.workspace_id
    connector = ingestion_pipeline._build_connector(source)
    log.info(
        "Reprocess: %d flagged evidence for source %s%s",
        len(pending), source.identifier, " (dry run)" if dry_run else "",
    )

    for stable_id in pending:
        try:
            msg = await connector.fetch_one(stable_id)
        except Exception as e:
            # A rejected session or an exhausted account would fail every one
            # after it the same way: stop, as the fetch does (ADR 0005).
            if credentials_rejected(e) or is_fatal_for_run(e):
                raise
            # Anything else is about this one message, and must not stop the rest.
            report.errors += 1
            log.error("Reprocess could not fetch %s: %s", stable_id, e, exc_info=True)
            continue
        if msg is None:
            report.unreachable.append(stable_id)
            continue
        report.matched += 1
        try:
            if not dry_run:
                await _replay_matched(db, msg, workspace_id, report)
        finally:
            # A video comes back downloaded to a temp file, dry run or not.
            ingestion_pipeline.discard_download(msg)

    report.unreachable.sort()
    log.info("Reprocess: %s", report.summary())
    return report


async def _replay_matched(db, msg, workspace_id, report) -> None:
    """Replay one of ours and commit it; a failure costs only that one."""
    from app.services import ingestion_pipeline

    try:
        if msg.content_type == "video":
            await _replay_video(db, msg, workspace_id, report)
        else:
            await _replay_one(
                db, msg, workspace_id, report,
                detect_lang=ingestion_pipeline._detect_lang,
                distill=ingestion_pipeline._distill_and_embed,
            )
        await db.commit()
    except Exception as e:
        log.error("Reprocess failed for %s: %s", msg.stable_id, e, exc_info=True)
        await db.rollback()
        if is_fatal_for_run(e):
            raise                   # every message after it would fail the same way
        report.errors += 1


async def _load_flagged(
    db: AsyncSession, source_config_id: uuid.UUID, limit: int | None
) -> list[str]:
    """The stable ids of this source's flagged evidence, newest first."""
    stmt = (
        select(EvidenceItem.stable_id)
        .where(
            EvidenceItem.source_config_id == source_config_id,
            EvidenceItem.needs_reprocessing.is_(True),
            REPLAYABLE,
        )
        .order_by(EvidenceItem.message_timestamp.desc())   # newest first: most useful evidence
    )
    if limit:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars())


async def _replay_one(db, msg, workspace_id, report, *, detect_lang, distill) -> None:
    """Re-run vision for one message and update its existing row."""
    meta = msg.metadata or {}
    b64 = meta.get("image_base64")
    if not b64:
        report.errors += 1
        return

    import base64
    result, usage = await vision_service.extract_image(
        base64.b64decode(b64), meta.get("image_mime", "image/jpeg")
    )

    item = await _stored(db, workspace_id, msg.stable_id)
    if item is None:
        report.errors += 1
        return

    description = result.get("description") or ""
    ocr_text = result.get("ocr_text") or ""
    content = "\n\n".join(filter(None, [description, ocr_text])) or "No trading content detected."

    item.content = content
    item.confidence = float(result.get("confidence", 1.0))
    item.is_on_topic = result.get("is_on_topic")
    item.relevance_reason = result.get("reason", "")
    item.original_language = detect_lang(content) if content else None
    item.needs_reprocessing = bool(result.get("needs_reprocessing", False))

    # Before the flush, which can raise: the vision call is already paid for (#27).
    await log_usage_event(
        workspace_id=workspace_id,
        task_type="vision",
        provider_model=settings.DISTILLATION_MODEL,
        input_units=usage["input_tokens"],
        output_units=usage["output_tokens"],
        cost_usd=vision_service.vision_cost(usage["input_tokens"], usage["output_tokens"]),
    )

    await db.flush()

    if item.needs_reprocessing:
        report.still_unreadable += 1
        return
    if not item.is_on_topic:
        report.recovered_off_topic += 1
        return

    if await already_distilled(db, item):
        report.recovered_on_topic += 1
        return

    await distill(db, item, msg, content, item.original_language)
    report.recovered_on_topic += 1


async def _replay_video(db, msg, workspace_id, report) -> None:
    """Analyse one downloaded video again and update its existing row."""
    from app.services import ingestion_pipeline

    item = await _stored(db, workspace_id, msg.stable_id)
    video_path = (msg.metadata or {}).get("video_path")
    if item is None or not video_path or not os.path.exists(video_path):
        report.errors += 1
        return

    # Vision is logged per frame inside, as each call returns (#27).
    for name, value in (await ingestion_pipeline.analyse_video(msg, video_path)).items():
        setattr(item, name, value)
    await db.flush()

    # A partly unreadable video is still judged on the frames that were read:
    # it counts as on or off topic, and as still unreadable as well.
    if item.needs_reprocessing:
        report.still_unreadable += 1
    if item.is_on_topic:
        report.recovered_on_topic += 1
    elif item.is_on_topic is False:
        report.recovered_off_topic += 1

    # As at ingestion: on-topic frames are distilled even if another frame was
    # unreadable, and the row stays flagged for that frame.
    if item.is_on_topic and not await already_distilled(db, item):
        await ingestion_pipeline._distill_and_embed(db, item, msg, item.content, item.original_language)


async def _stored(db, workspace_id, stable_id) -> EvidenceItem | None:
    return (await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.workspace_id == workspace_id,
            EvidenceItem.stable_id == stable_id,
        )
    )).scalar_one_or_none()


async def already_distilled(db, item) -> bool:
    """Guard against a second run duplicating what the first one distilled."""
    already = await db.execute(
        select(func.count()).select_from(TradeIdea).where(TradeIdea.evidence_item_id == item.id)
    )
    return bool(already.scalar())
