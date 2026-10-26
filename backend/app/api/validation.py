"""
Validation API — triggers outcome checking and win-rate computation for strategy cards.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.api.analysis_runs import AnalysisRunOut, already_running
from app.core.database import get_db
from app.models.analysis_run import VALIDATION
from app.services.analysis_run_service import RunAlreadyGoing, carry_out, start_run
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.models.outcome_check import OutcomeCheck

router = APIRouter(prefix="/workspaces", tags=["validation"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


class OutcomeCheckOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    trade_idea_id: uuid.UUID
    symbol: str | None
    action: str | None
    trade_date: date | None
    outcome: str
    outcome_date: date | None
    holding_days: int | None
    max_adverse_excursion: Decimal | None
    max_favorable_excursion: Decimal | None
    data_source: str
    checked_at: datetime
    window_days: int


@router.post("/{workspace_id}/validation/run", response_model=AnalysisRunOut, status_code=202)
async def trigger_validation(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Check every strategy card in this workspace against market prices, in the background.
    Answers with the run it started, which runs/latest then follows; refuses
    with 409 while the workspace has a run of either kind going (#86).
    """
    await _assert_member(db, workspace_id, current_user.id)
    try:
        run = await start_run(db, workspace_id, VALIDATION, current_user.id)
    except RunAlreadyGoing as refused:
        raise already_running(refused)
    background_tasks.add_task(carry_out, run_id=run.id, workspace_id=workspace_id, kind=VALIDATION)
    return run


@router.get("/{workspace_id}/validation/outcomes", response_model=list[OutcomeCheckOut])
async def list_outcomes(
    workspace_id: uuid.UUID,
    outcome: str | None = None,
    symbol: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List outcome checks for this workspace, optionally filtered."""
    await _assert_member(db, workspace_id, current_user.id)
    q = select(OutcomeCheck).where(OutcomeCheck.workspace_id == workspace_id)
    if outcome:
        q = q.where(OutcomeCheck.outcome == outcome)
    if symbol:
        q = q.where(OutcomeCheck.symbol == symbol.upper())
    q = q.order_by(OutcomeCheck.checked_at.desc()).limit(200)
    result = await db.execute(q)
    return result.scalars().all()
