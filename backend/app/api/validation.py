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

from app.core.database import get_db, AsyncSessionLocal
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


class ValidationResult(BaseModel):
    cards_processed: int
    message: str


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


@router.post("/{workspace_id}/validation/run", response_model=ValidationResult, status_code=202)
async def trigger_validation(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger outcome checking for all strategy cards in this workspace.
    Runs asynchronously — check strategy cards a minute later for updated win rates.
    """
    await _assert_member(db, workspace_id, current_user.id)
    background_tasks.add_task(_run_validation_bg, workspace_id=workspace_id)
    return ValidationResult(
        cards_processed=0,
        message="Validation started in background. Win rates will update within a minute.",
    )


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


async def _run_validation_bg(workspace_id: uuid.UUID):
    from app.services.validation_service import run_validation
    async with AsyncSessionLocal() as db:
        try:
            await run_validation(db, workspace_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Background validation failed for %s: %s", workspace_id, e)
