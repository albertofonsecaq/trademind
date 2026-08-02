"""
Changelog API — surfaces what changed on strategy cards and why, chronologically.
Reads from strategy_card.version_history (JSONB array) across all cards in the workspace.

Also exposes a combined recompute endpoint that chains mining → validation sequentially,
so a single button in the UI refreshes everything.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.models.strategy_card import StrategyCard

router = APIRouter(prefix="/workspaces", tags=["changelog"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


class ChangelogEntry(BaseModel):
    card_id: uuid.UUID
    symbol_scope: str
    setup_type: str
    version: int
    changed_at: str           # ISO string from snapshot
    change_source: str        # "mining" | "validation" | "unknown"
    changes_en: str
    sample_size: int | None
    win_rate: Decimal | None
    confidence_tier: str | None


class RecomputeResult(BaseModel):
    message: str


@router.get("/{workspace_id}/changelog", response_model=list[ChangelogEntry])
async def get_changelog(
    workspace_id: uuid.UUID,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a flat, chronologically sorted list of all strategy card changes.
    Derived from the version_history arrays stored on each card.
    """
    await _assert_member(db, workspace_id, current_user.id)

    result = await db.execute(
        select(StrategyCard).where(StrategyCard.workspace_id == workspace_id)
    )
    cards = result.scalars().all()

    entries: list[ChangelogEntry] = []
    for card in cards:
        for snap in (card.version_history or []):
            if not isinstance(snap, dict):
                continue
            changed_at = snap.get("snapshot_at") or ""
            entries.append(ChangelogEntry(
                card_id=card.id,
                symbol_scope=card.symbol_scope,
                setup_type=card.setup_type,
                version=int(snap.get("version", 0)),
                changed_at=changed_at,
                change_source=snap.get("change_source", "unknown"),
                changes_en=snap.get("changes_en") or snap.get("changes", "Updated"),
                sample_size=snap.get("sample_size"),
                win_rate=Decimal(snap["win_rate"]) if snap.get("win_rate") not in (None, "None") else None,
                confidence_tier=snap.get("confidence_tier"),
            ))

    # Sort newest-first, cap at limit
    entries.sort(key=lambda e: e.changed_at, reverse=True)
    return entries[:limit]


@router.post("/{workspace_id}/recompute", response_model=RecomputeResult, status_code=202)
async def trigger_recompute(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a full recomputation: pattern mining → validation (sequentially).
    Both steps must complete before strategy cards reflect new data.
    """
    await _assert_member(db, workspace_id, current_user.id)
    background_tasks.add_task(_recompute_bg, workspace_id=workspace_id)
    return RecomputeResult(
        message="Recomputation started (mining → validation). Strategy cards will update in ~30 seconds."
    )


async def _recompute_bg(workspace_id: uuid.UUID):
    import logging
    log = logging.getLogger(__name__)
    from app.services.pattern_mining_service import run_pattern_mining
    from app.services.validation_service import run_validation

    async with AsyncSessionLocal() as db:
        try:
            mined = await run_pattern_mining(db, workspace_id)
            log.info("Recompute: %d cards mined for workspace %s", mined, workspace_id)
        except Exception as e:
            log.error("Recompute mining failed for %s: %s", workspace_id, e)
            return

        try:
            validated = await run_validation(db, workspace_id)
            log.info("Recompute: %d cards validated for workspace %s", validated, workspace_id)
        except Exception as e:
            log.error("Recompute validation failed for %s: %s", workspace_id, e)
