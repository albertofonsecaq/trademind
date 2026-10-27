"""
Repair for link evidence stored as junk before #94.

A compressed page used to be read as text byte for byte. Where the junk held a
NUL the insert failed; where it did not, it was stored as the evidence's
content and judged like any other page. Each such row still carries its link,
so no connector is needed: the page is fetched again, the content replaced in
place, and the link judged again (#97).

Dry-run finds the rows without fetching a page or making a model call, so the
size of the repair can be checked for free before committing to the spend.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawMessage
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.models.trade_idea import TradeIdea
from app.models.workspace import Workspace
from app.services import url_service

log = logging.getLogger(__name__)


@dataclass
class LinkRepairReport:
    garbled: int = 0                # rows whose stored content is junk
    repaired_on_topic: int = 0
    repaired_off_topic: int = 0
    errors: int = 0
    unfetchable: list[str] = field(default_factory=list)   # page gone: row left as it was
    dry_run: bool = False

    def summary(self) -> str:
        head = "DRY RUN — no pages fetched, no model calls made" if self.dry_run else "repaired"
        return (
            f"{head}: garbled={self.garbled} on_topic={self.repaired_on_topic} "
            f"off_topic={self.repaired_off_topic} errors={self.errors} "
            f"unfetchable={len(self.unfetchable)}"
        )


async def repair_garbled_links(
    db: AsyncSession,
    *,
    source_config_id: uuid.UUID,
    limit: int | None = None,
    dry_run: bool = False,
) -> LinkRepairReport:
    """Fetch the garbled link evidence of one source again and replace it in place."""
    report = LinkRepairReport(dry_run=dry_run)
    garbled = await find_garbled_links(db, source_config_id, limit)
    report.garbled = len(garbled)
    if dry_run or not garbled:
        return report

    topic_scope = (await db.execute(
        select(Workspace.topic_scope)
        .join(SourceConfig, SourceConfig.workspace_id == Workspace.id)
        .where(SourceConfig.id == source_config_id)
    )).scalar_one()

    for item in garbled:
        stable_id = item.stable_id
        try:
            # A savepoint per row: a session rollback would detach every row
            # still waiting its turn, and the next one would fail for that (#2).
            async with db.begin_nested():
                await _repair_one(db, item, topic_scope, report)
            await db.commit()
        except Exception as e:
            report.errors += 1
            log.error("Link repair failed for %s: %s", stable_id, e, exc_info=True)

    log.info("Link repair: %s", report.summary())
    return report


async def find_garbled_links(
    db: AsyncSession, source_config_id: uuid.UUID, limit: int | None = None
) -> list[EvidenceItem]:
    """The link evidence of one source whose stored content is junk, newest first."""
    # Every junk row holds replacement characters, so the database narrows the
    # rows down and the share of junk decides.
    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.source_config_id == source_config_id,
            EvidenceItem.type == "url",
            EvidenceItem.content.contains("\ufffd"),
        )
        .order_by(EvidenceItem.message_timestamp.desc().nulls_last())
    )
    rows = [item for item in (await db.execute(stmt)).scalars() if url_service.looks_garbled(item.content)]
    return rows[:limit] if limit else rows


async def _repair_one(db, item: EvidenceItem, topic_scope: str, report: LinkRepairReport) -> None:
    from app.services import ingestion_pipeline

    meta = dict(item.source_metadata or {})
    fetched = await url_service.extract(meta.get("url") or "")
    if not fetched:
        report.unfetchable.append(item.stable_id)
        return

    item.content = fetched["text"]
    item.source_metadata = {**meta, "title": fetched.get("title")}
    item.original_language = ingestion_pipeline._detect_lang(item.content)
    await db.flush()

    # A junk row judged on topic may already have been distilled: no second idea
    # is added beside it, and the old one is left for a person to look at.
    already = (await db.execute(
        select(func.count()).select_from(TradeIdea).where(TradeIdea.evidence_item_id == item.id)
    )).scalar()
    await ingestion_pipeline._judge_link(db, item, _message_for(item), topic_scope, distill=not already)

    if item.is_on_topic:
        report.repaired_on_topic += 1
    else:
        report.repaired_off_topic += 1


def _message_for(item: EvidenceItem) -> RawMessage:
    """The message a stored link came from, as far as distillation needs one."""
    meta = item.source_metadata or {}
    return RawMessage(
        stable_id=item.stable_id,
        source_config_id=item.source_config_id,
        workspace_id=item.workspace_id,
        text=None,
        author=meta.get("author"),
        channel=meta.get("channel"),
        timestamp=item.message_timestamp,
        source_type=item.source_type,
        content_type="url",
        metadata=meta,
    )
