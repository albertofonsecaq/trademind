"""
The latest pattern-mining and validation run of a workspace — #86.

What the Strategy Library reads to show a run in progress and then how it
ended. Every member of the workspace sees the same runs.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.analysis_run import MINING, VALIDATION, RunKind, RunStatus
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.services.analysis_run_service import RunAlreadyGoing, latest_runs

router = APIRouter(prefix="/workspaces", tags=["analysis-runs"])


class AnalysisRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    kind: RunKind
    status: RunStatus
    triggered_by_user_id: uuid.UUID | None
    cards_created: int
    cards_updated: int
    failures: list[dict]
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class LatestRunsOut(BaseModel):
    mining: AnalysisRunOut | None
    validation: AnalysisRunOut | None


_LABELS = {MINING: "Pattern mining", VALIDATION: "Validation"}


def already_running(refused: RunAlreadyGoing) -> HTTPException:
    """The 409 a start answers with while the workspace has a run going, naming it."""
    return HTTPException(
        status_code=409,
        detail=f"{_LABELS[refused.running_kind]} is already running for this workspace",
    )


async def assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


@router.get("/{workspace_id}/runs/latest", response_model=LatestRunsOut)
async def latest(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await assert_member(db, workspace_id, current_user.id)
    runs = await latest_runs(db, workspace_id)
    return LatestRunsOut(
        mining=AnalysisRunOut.model_validate(runs[MINING]) if runs[MINING] else None,
        validation=AnalysisRunOut.model_validate(runs[VALIDATION]) if runs[VALIDATION] else None,
    )
