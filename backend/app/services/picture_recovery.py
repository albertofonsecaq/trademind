"""
Fetch again the pictures of image evidence ingested before they were kept (#111).

Until #108 an image was read by vision and its picture dropped, so image
evidence from before then has a description and nothing to show. Each message
is fetched again from the platform by its own id, as the replay does (#104),
and only its picture is kept: the evidence's text, verdict and trade ideas stay
as they are, and no model is asked anything, so it costs nothing but the fetch.

What is looked for follows the rule pictures are kept by (ADR 0006): image
evidence on topic, or whose verdict could not be read, with no picture yet. A
dry run only counts them, with no platform call at all.
"""
from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.llm_errors import is_fatal_for_run
from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.services.connection_health import credentials_rejected

log = logging.getLogger(__name__)


@dataclass
class RecoveryReport:
    without_picture: int = 0    # image evidence that would be, or was, looked for
    stored: int = 0             # pictures fetched and kept
    failed: int = 0             # fetching or keeping it raised; the rest carried on
    # The platform no longer has the message, or it has no photo any more.
    missing: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        if self.dry_run:
            return f"DRY RUN — nothing fetched: {self.without_picture} image evidence without a picture"
        return (
            f"recovered pictures: looked_for={self.without_picture} stored={self.stored} "
            f"missing={len(self.missing)} failed={self.failed}"
        )


# Image evidence that should have a picture and has none. Off topic is never
# shown, so its picture is not kept (ADR 0006).
_SHOULD_HAVE_ONE = (
    EvidenceItem.type == "image",
    or_(EvidenceItem.is_on_topic.is_(True), EvidenceItem.is_on_topic.is_(None)),
    EvidenceImage.id.is_(None),
)


def _without_picture(source_config_id: uuid.UUID):
    """This source's image evidence missing its picture, newest first: most useful."""
    return (
        select(EvidenceItem.id, EvidenceItem.stable_id)
        .outerjoin(EvidenceImage, EvidenceImage.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.source_config_id == source_config_id, *_SHOULD_HAVE_ONE)
        .order_by(EvidenceItem.message_timestamp.desc())
    )


async def missing_by_source(db: AsyncSession) -> list:
    """Per source: its id, identifier, how many pictures are missing, and over what dates."""
    rows = await db.execute(
        select(
            EvidenceItem.source_config_id,
            SourceConfig.identifier,
            func.count(),
            func.min(EvidenceItem.message_timestamp),
            func.max(EvidenceItem.message_timestamp),
        )
        .join(SourceConfig, SourceConfig.id == EvidenceItem.source_config_id)
        .outerjoin(EvidenceImage, EvidenceImage.evidence_item_id == EvidenceItem.id)
        .where(*_SHOULD_HAVE_ONE)
        .group_by(EvidenceItem.source_config_id, SourceConfig.identifier)
        .order_by(func.count().desc())
    )
    return rows.all()


async def recover_pictures(
    db: AsyncSession,
    *,
    source_config_id: uuid.UUID,
    limit: int | None = None,
    dry_run: bool = False,
) -> RecoveryReport:
    """Fetch again, and keep, the pictures one source's image evidence is missing."""
    stmt = _without_picture(source_config_id)
    if limit:
        stmt = stmt.limit(limit)
    pending = (await db.execute(stmt)).all()
    report = RecoveryReport(without_picture=len(pending), dry_run=dry_run)
    if dry_run or not pending:
        return report

    from app.services import ingestion_pipeline

    source = (await db.execute(
        select(SourceConfig)
        .options(selectinload(SourceConfig.connection))
        .where(SourceConfig.id == source_config_id)
    )).scalar_one_or_none()
    if source is None:
        raise ValueError(f"Source {source_config_id} not found")
    connector = ingestion_pipeline._build_connector(source)

    for evidence_id, stable_id in pending:
        try:
            msg = await connector.fetch_one(stable_id)
        except Exception as e:
            # A rejected session or an exhausted account would fail every one
            # after it the same way: stop, as the replay does (ADR 0005).
            if credentials_rejected(e) or is_fatal_for_run(e):
                raise
            report.failed += 1
            log.error("Could not fetch %s again: %s", stable_id, e, exc_info=True)
            continue
        try:
            encoded = (msg.metadata or {}).get("image_base64") if msg else None
            if not encoded:
                report.missing.append(stable_id)
                continue
            item = await db.get(EvidenceItem, evidence_id)
            await ingestion_pipeline.keep_picture(
                db, item, base64.b64decode(encoded), msg.metadata.get("image_mime", "image/jpeg")
            )
            # One at a time: a run cut short keeps every picture it got.
            await db.commit()
            report.stored += 1
        except Exception as e:
            await db.rollback()
            report.failed += 1
            log.error("Could not keep the picture of %s: %s", stable_id, e, exc_info=True)
        finally:
            if msg is not None:
                ingestion_pipeline.discard_download(msg)

    report.missing.sort()
    log.info("Picture recovery: %s", report.summary())
    return report
