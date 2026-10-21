"""
Source management (tracked channels) + backfill job control.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, field_validator

from sqlalchemy.orm import selectinload

from app.core.database import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.source_config import SourceConfig
from app.models import backfill_job as job_status
from app.models.backfill_job import BackfillJob
from app.models.platform_connection import PlatformConnection
from app.services import source_resume_service as resume_service
from app.core.clock import as_utc, now_utc
from app.core.llm_errors import is_fatal_for_run, safe_failure_summary
from app.services.connection_health import (
    REJECTED_SUMMARY,
    credentials_rejected,
    mark_rejected,
)
from app.services.ingestion_gate import (
    CONNECTION_MISSING,
    CONNECTION_UNUSABLE,
    SOURCE_PAUSED,
    WORKSPACE_PAUSED,
    blocked_reason,
    connection_blocked_reason,
)
from app.services.source_resume_service import NewestEvidenceUnknown, ResumeMode

router = APIRouter(prefix="/workspaces", tags=["sources"])

# One sentence per reason, so the API says the same thing everywhere it refuses.
# The UI shows its own translated copy; these are for API clients and logs.
_REFUSAL_DETAIL = {
    SOURCE_PAUSED: "This source is paused. Resume it before fetching or backfilling.",
    WORKSPACE_PAUSED: (
        "Ingestion is paused for this workspace. Resume it before fetching or backfilling."
    ),
    CONNECTION_MISSING: (
        "This source has no platform connection, so there is nothing to fetch through. "
        "Connect one for its platform first."
    ),
    CONNECTION_UNUSABLE: (
        "This source's platform connection is not active, so it cannot fetch. "
        "Reconnect it first."
    ),
}


# Why a queued backfill was called off, written onto the job itself.
_CANCELLED_BECAUSE = {
    SOURCE_PAUSED: "Source was paused before this backfill ran.",
    WORKSPACE_PAUSED: "Workspace ingestion was paused before this backfill ran.",
    CONNECTION_MISSING: "Source lost its platform connection before this backfill ran.",
    CONNECTION_UNUSABLE: "Source's platform connection stopped being active before this backfill ran.",
}


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


async def _load_ingesting_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """
    The workspace, refusing if its ingestion switch is on.

    A workspace-level pause stops on-demand work too, not just the poll (#13),
    and a workspace that does not exist is a 404 rather than a silent pass.
    """
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.ingestion_paused:
        raise HTTPException(status_code=409, detail=_REFUSAL_DETAIL[WORKSPACE_PAUSED])
    return workspace


def _assert_not_paused(source: SourceConfig):
    """
    A paused source must cost nothing, so every path that would spend on it
    refuses — the request handlers here, and the background runners below, which
    re-read the source and can find it paused after the work was queued (#10).
    """
    if blocked_reason(source_paused=source.is_paused, workspace_paused=False):
        raise HTTPException(status_code=409, detail=_REFUSAL_DETAIL[SOURCE_PAUSED])


def _connection_status_of(source: SourceConfig) -> str | None:
    """
    The state of the platform connection this source fetches through, or None
    when it has none — which the gate reads as its own reason.

    A function here rather than a property on SourceConfig: every fake source in
    the offline tests would then have to carry the derived value, and a fake that
    carries the answer cannot check the code that works it out.
    """
    return source.connection.status if source.connection else None


def _blocked_now(source: SourceConfig, workspace: Workspace) -> str | None:
    """
    Every reason a re-read source must not be fetched, for the background
    runners. They ask after the work was queued, when the source may have been
    paused, or its platform connection revoked, in between (#10, #49).
    """
    return blocked_reason(
        source_paused=source.is_paused, workspace_paused=workspace.ingestion_paused
    ) or connection_blocked_reason(
        source_type=source.source_type,
        connection_status=_connection_status_of(source),
    )


def _assert_connection_usable(source: SourceConfig):
    """
    Refuse work this source cannot do. Accepting it was the whole of #49: the
    request returned 202, the runner could not build a connector, and the user
    was left with a button that took the work and dropped it.

    A 409 rather than a 400: the request is fine, the source's state is not.
    """
    reason = connection_blocked_reason(
        source_type=source.source_type,
        connection_status=_connection_status_of(source),
    )
    if reason:
        raise HTTPException(status_code=409, detail=_REFUSAL_DETAIL[reason])


class _ReadsTimestampsAsUtc(BaseModel):
    """
    Every datetime a client sends is read as aware UTC.

    A payload without an offset parses as naive, and a naive value written to a
    `timestamptz` column is read in the *server's* local time zone — so the same
    request would store a different instant depending on where the process runs.
    Reading it as UTC here keeps the meaning these fields have always had (ADR 1).

    Reaches top-level datetime fields only. A datetime inside a list or a nested
    model would pass through untouched, so a request model that grows one needs
    its own validator — there are none today.
    """

    @field_validator("*", mode="after")
    @classmethod
    def _as_utc(cls, value):
        return as_utc(value) if isinstance(value, datetime) else value


class SourceCreateRequest(_ReadsTimestampsAsUtc):
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
    is_paused: bool
    paused_at: datetime | None
    last_fetched_at: datetime | None
    platform_connection_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResumeRequest(BaseModel):
    # No default: a resume that picks for the user is the thing #12 exists to
    # prevent, so an omitted mode is a 422, not a guess.
    mode: ResumeMode


class BackfillRequest(_ReadsTimestampsAsUtc):
    date_range_start: datetime
    date_range_end: datetime | None = None


class BackfillJobOut(BaseModel):
    id: uuid.UUID
    status: str
    items_ingested: int
    items_failed: int
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
        backfill_start_date=payload.backfill_start_date,
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


class SourceUpdateRequest(_ReadsTimestampsAsUtc):
    label: str | None = None
    fetch_cadence: str | None = None
    content_filters: dict | None = None
    backfill_start_date: datetime | None = None
    is_paused: bool | None = None


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
        source.backfill_start_date = payload.backfill_start_date
    if payload.is_paused is not None:
        if not payload.is_paused:
            # Resuming needs a mode, and this field cannot carry one (#12).
            raise HTTPException(
                status_code=400,
                detail="Resuming needs a mode. POST .../resume with catch_up or from_now.",
            )
        # last_fetched_id / last_fetched_at are deliberately left alone: the cursor
        # is what lets a resumed source continue exactly where it stopped, so a
        # pause is never a reset (#10).
        if not source.is_paused:
            source.is_paused = True
            source.paused_at = now_utc()
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
    await _load_ingesting_workspace(db, workspace_id)
    result = await db.execute(
        select(SourceConfig)
        .options(selectinload(SourceConfig.connection))
        .where(SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    _assert_not_paused(source)
    _assert_connection_usable(source)

    background_tasks.add_task(_run_fetch_bg, source_id=source_id, workspace_id=workspace_id)
    return {"message": "Fetch triggered in background"}


@router.post("/{workspace_id}/sources/{source_id}/resume", response_model=SourceOut)
async def resume_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    payload: ResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Resume a paused source, either catching up on the backlog or skipping to the
    newest item. The mode is required; see ResumeRequest (#12).
    """
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(SourceConfig)
        .options(selectinload(SourceConfig.connection))
        .where(SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not source.is_paused:
        raise HTTPException(status_code=409, detail="Source is not paused.")

    try:
        await resume_service.resume_source(source=source, mode=payload.mode)
    except NewestEvidenceUnknown:
        # Left paused on purpose: guessing a cursor here could re-ingest the
        # whole history, and ignoring the request would be a silent catch-up.
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not read the newest content for this source, so it stays paused. "
                "Try again, or resume with catch_up."
            ),
        )

    await db.commit()
    await db.refresh(source)
    return source


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
    await _load_ingesting_workspace(db, workspace_id)
    result = await db.execute(
        select(SourceConfig)
        .options(selectinload(SourceConfig.connection))
        .where(SourceConfig.id == source_id, SourceConfig.workspace_id == workspace_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    _assert_not_paused(source)
    _assert_connection_usable(source)

    job = BackfillJob(
        source_config_id=source_id,
        workspace_id=workspace_id,
        date_range_start=payload.date_range_start,
        date_range_end=payload.date_range_end,
        status=job_status.QUEUED,
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
    import logging
    log = logging.getLogger(__name__)
    from app.services.ingestion_pipeline import run_fetch_pipeline
    from sqlalchemy.orm import selectinload
    try:
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
            if source is not None and workspace is not None:
                reason = _blocked_now(source, workspace)
                if reason:
                    log.info("Skipping queued fetch for source %s: %s", source_id, reason)
                    return
            if source and workspace:
                outcome = await run_fetch_pipeline(db, source=source, workspace=workspace)
                log.info(
                    "Fetch complete for source %s: %d ingested, %d failed",
                    source_id, outcome.ingested, outcome.failed,
                )
    except Exception as e:
        log.error("Fetch background task failed for source %s: %s", source_id, e, exc_info=True)


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

        job.status = job_status.RUNNING
        await db.commit()

        # Bound before the try, because the handler at the bottom reports them:
        # a failure before the loop would otherwise make that handler die of an
        # unbound name while trying to explain itself.
        count = 0
        failed = 0
        connection_id = None

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
            reason = _blocked_now(source, workspace)
            if reason:
                # Queued while runnable, blocked before it got here: cancel
                # rather than spend, and say which it was on the job so the
                # reason outlives the logs. Nothing already ingested is removed
                # (#10, #13, #49).
                message = _CANCELLED_BECAUSE[reason]
                log.info("Cancelling backfill job %s: %s", job_id, message)
                job.status = job_status.CANCELLED
                job.error_message = message
                job.completed_at = now_utc()
                await db.commit()
                return

            connector = _build_connector(source)
            # Read every ORM attribute the loop needs up front: a commit or rollback
            # inside it can expire these objects, and re-reading them then triggers
            # a sync lazy-load that raises MissingGreenlet (#2).
            filters = source.content_filters or {}
            topic_scope = workspace.topic_scope
            date_start, date_end = job.date_range_start, job.date_range_end
            # The foreign key, for the same reason the fetch path reads it:
            # enough on its own to mark the connection if Telegram rejects the
            # session mid-range (#54).
            connection_id = source.platform_connection_id

            first_failure: str | None = None
            fatal: Exception | None = None
            stopped_at: datetime | None = None

            async for msg in connector.fetch_range(date_start, date_end):
                if not _content_type_allowed(msg, filters):
                    continue
                try:
                    # Savepoint per message — a session rollback would expire the
                    # job and source rows this loop still depends on.
                    async with db.begin_nested():
                        item = await process_message(db, msg, topic_scope)
                    if item:
                        count += 1
                    await db.commit()
                except Exception as e:
                    failed += 1
                    if first_failure is None:
                        first_failure = safe_failure_summary(e)
                    log.error(
                        "Backfill pipeline error on %s: %s", msg.stable_id, e, exc_info=True
                    )
                    if is_fatal_for_run(e):
                        # Every remaining message hits the same wall, so walking
                        # the rest of the range buys nothing but wall-clock time
                        # and a log full of the same line (#9).
                        fatal = e
                        stopped_at = msg.timestamp
                        break

            # A row has to be readable on its own: "completed" used to be written
            # whether a run lost nothing or lost most of what it saw (#9).
            if fatal is not None:
                job.status = job_status.FAILED
                # The failure that ended the run, not the first unrelated one —
                # and where it stopped, because the range after that point was
                # never attempted and the row would otherwise claim it.
                job.error_message = (
                    f"{safe_failure_summary(fatal)} Stopped at "
                    f"{stopped_at.isoformat() if stopped_at else 'an unknown point'}; "
                    "the rest of the range was not attempted."
                )[:900]
            else:
                job.status = job_status.PARTIAL if failed else job_status.COMPLETED
                if first_failure:
                    job.error_message = first_failure[:900]
            job.items_ingested = count
            job.items_failed = failed
            job.completed_at = now_utc()
            log.info(
                "Backfill job %s %s: %d ingested, %d failed",
                job_id, job.status, count, failed,
            )

        except Exception as e:
            log.error("Backfill job %s failed: %s", job_id, e, exc_info=True)
            job.status = job_status.FAILED
            if credentials_rejected(e):
                # Written onto the connection, which is what stops the source
                # being offered again — see connection_health (#54).
                job.error_message = REJECTED_SUMMARY
                await mark_rejected(db, connection_id)
            else:
                job.error_message = safe_failure_summary(e)[:900]
            # A row has to be readable on its own (#9): a run that ingested 200
            # messages and then lost the session said FAILED, 0 ingested, never
            # completed — three claims, two of them false. This path used to be
            # only for exceptions nothing expected; a rejected session made it
            # ordinary.
            job.items_ingested = count
            job.items_failed = failed
            job.completed_at = now_utc()

        await db.commit()
