"""
Targeted replay for evidence items flagged needs_reprocessing.

Why this exists rather than a backfill: run_fetch_pipeline INSERTs, so replaying
a message that is already stored raises IntegrityError on uq_evidence_stable_id
and is skipped — after the vision call has already been paid for. This walks the
same connector but matches against the flagged stable_ids, skips everything else
before any model call, and UPDATEs the row in place.

Dry-run does the connector scan without a single model call, so recoverability
can be checked for free before committing to the spend.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.models.trade_idea import TradeIdea
from app.services import vision_service
from app.services.usage_service import log_usage_event

log = logging.getLogger(__name__)

# The connector filters by date, not by id, so widen the window slightly to
# absorb any timestamp skew between what we stored and what the source reports.
_WINDOW_PADDING = timedelta(days=1)


def _naive_utc(dt: datetime) -> datetime:
    """Connectors yield naive UTC (telegram.py:_ts) but these columns are timestamptz,
    so reads come back aware. Match the connector or the range comparison blows up."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@dataclass
class ReprocessReport:
    flagged: int = 0            # items asked for
    scanned: int = 0            # messages the connector yielded
    matched: int = 0            # messages that were one of ours
    recovered_on_topic: int = 0
    recovered_off_topic: int = 0
    still_unreadable: int = 0   # vision failed again — stays flagged
    errors: int = 0
    unreachable: list[str] = field(default_factory=list)  # never seen in the range
    dry_run: bool = False

    def summary(self) -> str:
        head = "DRY RUN — no model calls made" if self.dry_run else "reprocessed"
        return (
            f"{head}: flagged={self.flagged} scanned={self.scanned} matched={self.matched} "
            f"on_topic={self.recovered_on_topic} off_topic={self.recovered_off_topic} "
            f"still_unreadable={self.still_unreadable} errors={self.errors} "
            f"unreachable={len(self.unreachable)}"
        )


async def reprocess_flagged_images(
    db: AsyncSession,
    *,
    source_config_id: uuid.UUID,
    limit: int | None = None,
    dry_run: bool = False,
) -> ReprocessReport:
    """Re-run vision over the flagged images of one source and update them in place."""
    from app.services.ingestion_pipeline import _build_connector, _detect_lang, _distill_and_embed

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
    timestamps = [ts for ts in pending.values() if ts is not None]
    if not timestamps:
        raise ValueError("Flagged items carry no message_timestamp — cannot bound the scan")
    date_start = _naive_utc(min(timestamps)) - _WINDOW_PADDING
    date_end = _naive_utc(max(timestamps)) + _WINDOW_PADDING

    connector = _build_connector(source)
    log.info(
        "Reprocess: %d flagged image(s) for source %s, scanning %s → %s%s",
        len(pending), source.identifier, date_start.date(), date_end.date(),
        " (dry run)" if dry_run else "",
    )

    outstanding = set(pending)
    async for msg in connector.fetch_range(date_start, date_end):
        report.scanned += 1
        if msg.stable_id not in outstanding:
            continue            # not ours — drop it before spending anything
        if msg.content_type != "image":
            continue
        outstanding.discard(msg.stable_id)
        report.matched += 1

        if dry_run:
            continue
        try:
            await _replay_one(
                db, msg, workspace_id, report,
                detect_lang=_detect_lang, distill=_distill_and_embed,
            )
            await db.commit()
        except Exception as e:
            report.errors += 1
            log.error("Reprocess failed for %s: %s", msg.stable_id, e, exc_info=True)
            await db.rollback()

        if not outstanding:
            break

    report.unreachable = sorted(outstanding)
    log.info("Reprocess: %s", report.summary())
    return report


async def _load_flagged(
    db: AsyncSession, source_config_id: uuid.UUID, limit: int | None
) -> dict[str, object]:
    """stable_id → message_timestamp for the flagged images of this source."""
    stmt = (
        select(EvidenceItem.stable_id, EvidenceItem.message_timestamp)
        .where(
            EvidenceItem.source_config_id == source_config_id,
            EvidenceItem.needs_reprocessing.is_(True),
            EvidenceItem.type == "image",
        )
        .order_by(EvidenceItem.message_timestamp.desc())   # newest first: most useful evidence
    )
    if limit:
        stmt = stmt.limit(limit)
    return {row.stable_id: row.message_timestamp for row in (await db.execute(stmt)).all()}


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

    item = (await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.workspace_id == workspace_id,
            EvidenceItem.stable_id == msg.stable_id,
        )
    )).scalar_one_or_none()
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
    await db.flush()

    await log_usage_event(
        db,
        workspace_id=workspace_id,
        task_type="vision",
        provider_model=settings.DISTILLATION_MODEL,
        input_units=usage["input_tokens"],
        output_units=usage["output_tokens"],
        cost_usd=vision_service.vision_cost(usage["input_tokens"], usage["output_tokens"]),
    )

    if item.needs_reprocessing:
        report.still_unreadable += 1
        return
    if not item.is_on_topic:
        report.recovered_off_topic += 1
        return

    # Guard against a second run duplicating what the first one distilled.
    already = await db.execute(
        select(func.count()).select_from(TradeIdea).where(TradeIdea.evidence_item_id == item.id)
    )
    if already.scalar():
        report.recovered_on_topic += 1
        return

    await distill(db, item, msg, content, item.original_language)
    report.recovered_on_topic += 1
