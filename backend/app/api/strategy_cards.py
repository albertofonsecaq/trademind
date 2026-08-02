"""
Strategy cards API — workspace-scoped, read-only for members.
Mining is triggered via POST /mining/run.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel

from app.core.database import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from app.models.strategy_card import StrategyCard
from app.models.trade_idea import TradeIdea
from app.models.evidence_item import EvidenceItem
from app.models.outcome_check import OutcomeCheck

router = APIRouter(prefix="/workspaces", tags=["strategy-cards"])


async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


class FlowchartSpec(BaseModel):
    entry: str = ""
    confirmation: str = ""
    risk_management: str = ""
    exit: str = ""


class StrategyCardOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    workspace_id: uuid.UUID
    setup_type: str
    symbol_scope: str
    description_en: str
    description_es: str
    flowchart_spec: dict
    supporting_evidence: list
    win_rate: Decimal | None
    sample_size: int
    confidence_interval: dict | None
    preliminary_confidence: Decimal
    source_count: int
    walk_forward_result: dict | None
    validation_updated_at: datetime | None
    version: int
    version_history: list
    last_updated: datetime
    created_at: datetime


class MiningResult(BaseModel):
    cards_touched: int
    message: str


@router.get("/{workspace_id}/strategy-cards", response_model=list[StrategyCardOut])
async def list_strategy_cards(
    workspace_id: uuid.UUID,
    symbol: str | None = None,
    setup_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    q = select(StrategyCard).where(StrategyCard.workspace_id == workspace_id)
    if symbol:
        q = q.where(StrategyCard.symbol_scope == symbol.upper())
    if setup_type:
        q = q.where(StrategyCard.setup_type == setup_type)
    q = q.order_by(StrategyCard.preliminary_confidence.desc(), StrategyCard.sample_size.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{workspace_id}/strategy-cards/{card_id}", response_model=StrategyCardOut)
async def get_strategy_card(
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    result = await db.execute(
        select(StrategyCard).where(
            StrategyCard.id == card_id,
            StrategyCard.workspace_id == workspace_id,
        )
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Strategy card not found")
    return card


@router.post("/{workspace_id}/mining/run", response_model=MiningResult, status_code=202)
async def trigger_mining(
    workspace_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, workspace_id, current_user.id)
    background_tasks.add_task(_run_mining_bg, workspace_id=workspace_id)
    return MiningResult(
        cards_touched=0,
        message="Pattern mining started in background. Check strategy cards in a few seconds.",
    )


class CitedExample(BaseModel):
    """One trade_idea from the card's supporting evidence, with source context."""
    id: uuid.UUID
    symbol: str | None
    action: str | None
    setup_type: str | None
    summary_en: str | None
    summary_es: str | None
    original_text: str | None
    source_language: str | None
    author: str | None
    channel: str | None
    message_timestamp: datetime | None
    source_type: str | None
    source_metadata: dict | None    # includes video_url (YouTube) or message_id (Telegram)
    outcome: str | None             # from outcome_check if available


class OutcomeSummary(BaseModel):
    won: int = 0
    lost: int = 0
    open: int = 0
    expired: int = 0
    inconclusive: int = 0
    total: int = 0


class StrategyCardDetail(BaseModel):
    card: StrategyCardOut
    examples: list[CitedExample]
    outcome_summary: OutcomeSummary


@router.get("/{workspace_id}/strategy-cards/{card_id}/detail", response_model=StrategyCardDetail)
async def get_strategy_card_detail(
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    examples_limit: int = Query(default=10, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Full teaching view for one strategy card:
    - Card with all fields
    - Up to examples_limit cited trade_idea examples with original text + source links
    - Outcome summary (won/lost/open/expired/inconclusive counts)
    """
    await _assert_member(db, workspace_id, current_user.id)

    card_result = await db.execute(
        select(StrategyCard).where(
            StrategyCard.id == card_id, StrategyCard.workspace_id == workspace_id
        )
    )
    card = card_result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Strategy card not found")

    # Fetch cited trade_ideas (newest first, capped)
    all_idea_ids = [uuid.UUID(i) for i in (card.supporting_evidence or []) if i]
    sample_ids = all_idea_ids[:examples_limit]

    examples: list[CitedExample] = []
    if sample_ids:
        rows = await db.execute(
            select(TradeIdea, EvidenceItem, OutcomeCheck)
            .join(EvidenceItem, EvidenceItem.id == TradeIdea.evidence_item_id)
            .outerjoin(OutcomeCheck, OutcomeCheck.trade_idea_id == TradeIdea.id)
            .where(TradeIdea.id.in_(sample_ids))
            .order_by(TradeIdea.message_timestamp.desc())
        )
        for idea, evidence, outcome in rows.all():
            examples.append(CitedExample(
                id=idea.id,
                symbol=idea.symbol,
                action=idea.action,
                setup_type=idea.setup_type,
                summary_en=idea.summary_en,
                summary_es=idea.summary_es,
                original_text=idea.original_text,
                source_language=idea.source_language,
                author=idea.author,
                channel=idea.channel,
                message_timestamp=idea.message_timestamp,
                source_type=evidence.source_type if evidence else None,
                source_metadata=evidence.source_metadata if evidence else None,
                outcome=outcome.outcome if outcome else None,
            ))

    # Outcome summary across ALL supporting_evidence (not just the sample)
    summary = OutcomeSummary(total=len(all_idea_ids))
    if all_idea_ids:
        counts_result = await db.execute(
            select(OutcomeCheck.outcome, func.count().label("cnt"))
            .where(OutcomeCheck.trade_idea_id.in_(all_idea_ids))
            .group_by(OutcomeCheck.outcome)
        )
        for row in counts_result.all():
            outcome_str, cnt = row.outcome, int(row.cnt)
            if outcome_str == "won":       summary.won = cnt
            elif outcome_str == "lost":    summary.lost = cnt
            elif outcome_str == "open":    summary.open = cnt
            elif outcome_str == "expired": summary.expired = cnt
            else:                          summary.inconclusive += cnt

    return StrategyCardDetail(card=StrategyCardOut.model_validate(card), examples=examples, outcome_summary=summary)


class ExecutionStats(BaseModel):
    """Personal execution stats for one strategy card — always scoped to current user."""
    total: int = 0
    won: int = 0
    lost: int = 0
    open: int = 0
    expired: int = 0
    inconclusive: int = 0
    win_rate: float | None = None
    entries: list[dict] = []


@router.get("/{workspace_id}/strategy-cards/{card_id}/my-execution", response_model=ExecutionStats)
async def get_my_execution(
    workspace_id: uuid.UUID,
    card_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the current user's personal execution stats for one strategy card —
    how many times they traded it, their win rate, and the individual journal entries.
    Always user-scoped; never shares data with other workspace members.
    """
    await _assert_member(db, workspace_id, current_user.id)

    # Verify card belongs to this workspace
    card_result = await db.execute(
        select(StrategyCard).where(
            StrategyCard.id == card_id, StrategyCard.workspace_id == workspace_id
        )
    )
    if not card_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Strategy card not found")

    from app.services.journal_validation_service import get_execution_stats
    stats = await get_execution_stats(db, current_user.id, card_id)
    return ExecutionStats(**stats)


async def _run_mining_bg(workspace_id: uuid.UUID):
    from app.services.pattern_mining_service import run_pattern_mining
    async with AsyncSessionLocal() as db:
        try:
            await run_pattern_mining(db, workspace_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Background mining failed for %s: %s", workspace_id, e)
