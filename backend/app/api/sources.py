"""
Source management (tracked channels) + backfill job control.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.source_config import SourceConfig
from app.models.backfill_job import BackfillJob
from app.models.platform_connection import PlatformConnection

router = APIRouter(prefix="/workspaces", tags=["sources"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


class SourceCreateRequest(BaseModel):
    source_type: str
    identifier: str
    label: str | None = None
    platform_connection_id: uuid.UUID | None = None
    fetch_cadence: str = "hourly"
    content_filters: dict = {"text": True, "image": False, "video": False, "url": False}
    backfill_start_date: datetime | None = None


class SourceOut(BaseModel):
    id: uuid.UUID
    source_type: str
    identifier: str
    label: str | None
    fetch_cadence: str
    content_filters: dict
    backfill_start_date: datetime | None
    last_fetched_at: datetime | None
    platform_connection_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BackfillRequest(BaseModel):
    date_range_start: datetime
    date_range_end: datetime | None = None


class BackfillJobOut(BaseModel):
    id: uuid.UUID
    status: str
    items_ingested: int
    date_range_start: datetime
    date_range_end: datetime | None
    created_at: datetime
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


@router.post("/{workspace_id}/sources", response_model=SourceOut, status_code=201)
async def add_source(
    workspace_id: uuid.UUID,
    payload: SourceCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)

    # Validate connection belongs to this workspace
    if payload.platform_connection_id:
        conn_result = await db.execute(
            select(PlatformConnection).where(
                PlatformConnection.id == payload.platform_connection_id,
                PlatformConnection.workspace_id == workspace_id,
                PlatformConnection.status == "active",
            )
        )
        if not conn_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Connection not found or not active")

    source = SourceConfig(
        workspace_id=workspace_id,
        platform_connection_id=payload.platform_connection_id,
        source_type=payload.source_type,
        identifier=payload.identifier,
        label=payload.label,
        fetch_cadence=payload.fetch_cadence,
        content_filters=payload.content_filters,
        backfill_start_date=payload.backfill_start_date.replace(tzinfo=None) if payload.backfill_start_date else None,
        created_by_user_id=current_user.id,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.get("/{workspace_id}/sources", response_model=list[SourceOut])
async def list_sources(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig).where(SourceConfig.workspace_id == workspace_id)
    )
    return result.scalars().all()


class SourceUpdateRequest(BaseModel):
    label: str | None = None
    fetch_cadence: str | None = None
    content_filters: dict | None = None
    backfill_start_date: datetime | None = None


@router.patch("/{workspace_id}/sources/{source_id}", response_model=SourceOut)
async def update_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    payload: SourceUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig).where(
            SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if payload.label is not None:
        source.label = payload.label
    if payload.fetch_cadence is not None:
        source.fetch_cadence = payload.fetch_cadence
    if payload.content_filters is not None:
        source.content_filters = payload.content_filters
    if payload.backfill_start_date is not None:
        source.backfill_start_date = payload.backfill_start_date.replace(tzinfo=None)
    await db.commit()
    await db.refresh(source)
    return source


@router.delete("/{workspace_id}/sources/{source_id}", status_code=204)
async def remove_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig).where(
            SourceConfig.id == source_id,
            SourceConfig.workspace_id == workspace_id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()


@router.post("/{workspace_id}/sources/{source_id}/fetch", status_code=202)
async def trigger_fetch(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate fetch of new messages from this source."""
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig).where(
            SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    background_tasks.add_task(_run_fetch_bg, source_id=source_id, workspace_id=workspace_id)
    return {"message": "Fetch triggered in background"}


@router.post("/{workspace_id}/sources/{source_id}/backfill", response_model=BackfillJobOut, status_code=201)
async def trigger_backfill(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    payload: BackfillRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig).where(
            SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Source not found")

    job = BackfillJob(
        source_config_id=source_id,
        workspace_id=workspace_id,
        date_range_start=payload.date_range_start.replace(tzinfo=None),
        date_range_end=payload.date_range_end.replace(tzinfo=None) if payload.date_range_end else None,
        status="queued",
        triggered_by_user_id=current_user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(_run_backfill_bg, job_id=job.id)
    return job


@router.get("/{workspace_id}/sources/{source_id}/backfill-jobs", response_model=list[BackfillJobOut])
async def list_backfill_jobs(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(BackfillJob).where(
            BackfillJob.source_config_id == source_id,
            BackfillJob.workspace_id == workspace_id,
        ).order_by(BackfillJob.created_at.desc())
    )
    return result.scalars().all()


# --- background task runners ---

async def _run_fetch_bg(source_id: uuid.UUID, workspace_id: uuid.UUID):
    from app.services.ingestion_pipeline import run_fetch_pipeline
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as db:
        source_result = await db.execute(
            select(SourceConfig)
            .options(selectinload(SourceConfig.connection))
            .where(SourceConfig.id == source_id)
        )
        source = source_result.scalar_one_or_none()
        workspace_result = await db.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = workspace_result.scalar_one_or_none()
        if source and workspace:
            await run_fetch_pipeline(db, source=source, workspace=workspace)


async def _run_backfill_bg(job_id: uuid.UUID):
    import logging
    log = logging.getLogger(__name__)
    from sqlalchemy.orm import selectinload
    from app.services.ingestion_pipeline import process_message, _build_connector, _content_type_allowed

    async with AsyncSessionLocal() as db:
        job_result = await db.execute(select(BackfillJob).where(BackfillJob.id == job_id))
        job = job_result.scalar_one_or_none()
        if not job:
            return

        job.status = "running"
        await db.commit()

        try:
            source_result = await db.execute(
                select(SourceConfig)
                .options(selectinload(SourceConfig.connection))
                .where(SourceConfig.id == job.source_config_id)
            )
            source = source_result.scalar_one_or_none()
            workspace_result = await db.execute(
                select(Workspace).where(Workspace.id == job.workspace_id)
            )
            workspace = workspace_result.scalar_one_or_none()
            if not source or not workspace:
                raise ValueError("Source or workspace missing")

            connector = _build_connector(source)
            filters = source.content_filters or {}

            count = 0
            async for msg in connector.fetch_range(job.date_range_start, job.date_range_end):
                if not _content_type_allowed(msg, filters):
                    continue
                try:
                    item = await process_message(db, msg, workspace.topic_scope)
                    if item:
                        count += 1
                    await db.commit()
                except Exception as e:
                    log.error("Backfill pipeline error: %s", e)
                    await db.rollback()

            job.status = "completed"
            job.items_ingested = count
            job.completed_at = datetime.utcnow()

        except Exception as e:
            log.error("Backfill job %s failed: %s", job_id, e)
            job.status = "failed"
            job.error_message = str(e)[:900]

        await db.commit()
