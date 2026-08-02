"""
Plan items — personal curation layer, always scoped to user_id.
Never visible to other workspace members regardless of membership.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.models.strategy_card import StrategyCard
from app.models.plan_item import PlanItem

router = APIRouter(prefix="/workspaces", tags=["plan-items"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


class PlanItemCreate(BaseModel):
    strategy_card_id: uuid.UUID
    status: str = "watching"
    user_notes: str | None = None
    risk_tolerance_match: str | None = None


class PlanItemUpdate(BaseModel):
    status: str | None = None
    user_notes: str | None = None
    risk_tolerance_match: str | None = None


class CardSummary(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    setup_type: str
    symbol_scope: str
    description_en: str
    description_es: str
    sample_size: int
    preliminary_confidence: float
    source_count: int


class PlanItemOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    strategy_card_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    user_notes: str | None
    risk_tolerance_match: str | None
    created_at: datetime
    updated_at: datetime
    card: CardSummary


_VALID_STATUSES = {"watching", "adopted", "rejected"}


@router.get("/{workspace_id}/plan-items", response_model=list[PlanItemOut])
async def list_plan_items(
    workspace_id: uuid.UUID,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    q = (
        select(PlanItem)
        .options(selectinload(PlanItem.card))
        .where(PlanItem.user_id == current_user.id)
        .join(StrategyCard, PlanItem.strategy_card_id == StrategyCard.id)
        .where(StrategyCard.workspace_id == workspace_id)
    )
    if status:
        q = q.where(PlanItem.status == status)
    q = q.order_by(PlanItem.updated_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{workspace_id}/plan-items", response_model=PlanItemOut, status_code=201)
async def add_plan_item(
    workspace_id: uuid.UUID,
    payload: PlanItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)

    # Verify the strategy card belongs to this workspace
    card_result = await db.execute(
        select(StrategyCard).where(
            StrategyCard.id == payload.strategy_card_id,
            StrategyCard.workspace_id == workspace_id,
        )
    )
    if not card_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Strategy card not found in this workspace")

    if payload.status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {_VALID_STATUSES}")

    # Check for existing item (unique constraint user_id + strategy_card_id)
    existing = await db.execute(
        select(PlanItem).where(
            PlanItem.user_id == current_user.id,
            PlanItem.strategy_card_id == payload.strategy_card_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This strategy card is already in your plan")

    item = PlanItem(
        strategy_card_id=payload.strategy_card_id,
        user_id=current_user.id,
        status=payload.status,
        user_notes=payload.user_notes,
        risk_tolerance_match=payload.risk_tolerance_match,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(item)
    await db.commit()

    result = await db.execute(
        select(PlanItem).options(selectinload(PlanItem.card)).where(PlanItem.id == item.id)
    )
    return result.scalar_one()


@router.patch("/{workspace_id}/plan-items/{item_id}", response_model=PlanItemOut)
async def update_plan_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: PlanItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)

    result = await db.execute(
        select(PlanItem)
        .options(selectinload(PlanItem.card))
        .where(PlanItem.id == item_id, PlanItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Plan item not found")

    if payload.status is not None:
        if payload.status not in _VALID_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {_VALID_STATUSES}")
        item.status = payload.status
    if payload.user_notes is not None:
        item.user_notes = payload.user_notes
    if payload.risk_tolerance_match is not None:
        item.risk_tolerance_match = payload.risk_tolerance_match

    item.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{workspace_id}/plan-items/{item_id}", status_code=204)
async def remove_plan_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(PlanItem).where(PlanItem.id == item_id, PlanItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Plan item not found")
    await db.delete(item)
    await db.commit()
