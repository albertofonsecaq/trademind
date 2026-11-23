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
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from telethon.errors import ChannelPrivateError, FloodWaitError

from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
from app.models.source_config import SourceConfig
from app.services.connection_health import credentials_rejected
from app.services.ingestion_pipeline import KEEPS_A_PICTURE

log = logging.getLogger(__name__)


@dataclass
class RecoveryReport:
    found: int = 0              # image evidence found without its picture
    stored: int = 0             # pictures fetched and kept
    off_topic: int = 0          # judged off topic since it was found: not kept (ADR 0006)
    failed: int = 0             # fetching or keeping it raised; the rest carried on
    # The platform no longer has the message, or it has no photo any more.
    missing: list[str] = field(default_factory=list)
    # Why the run ended before the last one, when something would have failed
    # every message after it. What it stored until then is kept.
    stopped_because: str | None = None
    dry_run: bool = False

    def summary(self) -> str:
        if self.dry_run:
            return f"DRY RUN — nothing fetched: {self.found} image evidence without its picture"
        line = (
            f"recovered pictures: found={self.found} stored={self.stored} "
            f"missing={len(self.missing)} off_topic={self.off_topic} failed={self.failed}"
        )
        return f"{line}\nSTOPPED: {self.stopped_because}" if self.stopped_because else line


def _stops_the_run(e: Exception) -> str | None:
    """
    Why `e` would fail every message after it too, or None if it is about one.

    No model is called here, so an exhausted AI account cannot happen; what can
    is Telegram turning the whole run away (ADR 0005).
    """
    if credentials_rejected(e):
        return f"Telegram rejected the session: {e}"
    if isinstance(e, ChannelPrivateError):
        return "the channel is private now, or this account was removed from it"
    if isinstance(e, FloodWaitError):
        return f"Telegram asked to wait {e.seconds} seconds before fetching more; run it again later"
    return None


# Image evidence that should have a picture, by the rule pictures are kept by,
# and has none.
_SHOULD_HAVE_ONE = (EvidenceItem.type == "image", KEEPS_A_PICTURE, EvidenceImage.id.is_(None))


class MissingPictures(NamedTuple):
    """One source's image evidence without its picture."""
    source_config_id: uuid.UUID
    identifier: str
    count: int
    earliest: datetime | None
    latest: datetime | None


def _without_picture(source_config_id: uuid.UUID):
    """This source's image evidence missing its picture, newest first: most useful."""
    return (
        select(EvidenceItem.id, EvidenceItem.stable_id)
        .outerjoin(EvidenceImage, EvidenceImage.evidence_item_id == EvidenceItem.id)
        .where(EvidenceItem.source_config_id == source_config_id, *_SHOULD_HAVE_ONE)
        .order_by(EvidenceItem.message_timestamp.desc())
    )


async def missing_by_source(db: AsyncSession) -> list[MissingPictures]:
    """Per source, how many pictures are missing and over what dates, most first."""
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
    return [MissingPictures(*row) for row in rows]


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
    report = RecoveryReport(found=len(pending), dry_run=dry_run)
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
    if not (source.content_filters or {}).get("image"):
        # Its connector would hand back nothing for every message, which would
        # read as every one of them deleted on Telegram.
        report.stopped_because = "the source no longer fetches images"
        return report
    connector = ingestion_pipeline._build_connector(source)

    for evidence_id, stable_id in pending:
        try:
            msg = await connector.fetch_one(stable_id)
        except Exception as e:
            reason = _stops_the_run(e)
            if reason:
                report.stopped_because = reason
                log.error("Picture recovery stopped: %s", reason)
                break
            report.failed += 1
            log.error("Could not fetch %s again: %s", stable_id, e, exc_info=True)
            continue
        try:
            encoded = (msg.metadata or {}).get("image_base64") if msg else None
            if not encoded:
                report.missing.append(stable_id)
                continue
            item = await db.get(EvidenceItem, evidence_id)
            kept = await ingestion_pipeline.keep_picture(
                db, item, base64.b64decode(encoded), msg.metadata.get("image_mime", "image/jpeg")
            )
            # One at a time: a run cut short keeps every picture it got.
            await db.commit()
            if kept:
                report.stored += 1
            else:
                report.off_topic += 1
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
