"""
Ask endpoint — the core query interface.
Budget/overage check runs before synthesis to enforce spend controls.
query_synthesis usage is tagged is_overage=True once included_budget is exhausted.
"""
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember  # noqa: F401 (used in _get_workspace_and_assert_member)
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent
from app.services import embedding_service, retrieval_service, query_synthesis_service
from app.services.usage_service import log_usage_event
from app.core.config import settings
from pydantic import BaseModel

router = APIRouter(prefix="/workspaces", tags=["ask"])


class AskRequest(BaseModel):
    query: str
    language: str = "en"


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]


async def _get_workspace_and_assert_member(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> Workspace:
    member = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not member.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    ws = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = ws.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


async def _check_overage(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Returns True if next query_synthesis call should be tagged as overage."""
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if not sub or sub.status != "active":
        return False  # No active subscription → no overage tracking

    if not sub.current_period_start:
        return False  # Period not yet established (subscription just created)

    ws_result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    ws = ws_result.scalar_one_or_none()
    budget_cap = ws.monthly_budget_cap if ws else sub.included_budget_usd

    # Sum all non-overage usage since the current period start
    period_cost = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.workspace_id == workspace_id,
            UsageEvent.is_overage == False,
            UsageEvent.created_at >= sub.current_period_start,
        )
    )
    spent = Decimal(str(period_cost.scalar()))
    return spent >= (budget_cap or sub.included_budget_usd)


async def _check_overage_ceiling(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Returns True if the hard overage ceiling is reached → pause query synthesis."""
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if not sub or sub.status != "active":
        return False
    return sub.current_period_overage_usd >= sub.overage_ceiling_usd


@router.post("/{workspace_id}/ask", response_model=AskResponse)
async def ask(
    workspace_id: uuid.UUID,
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workspace = await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    if not payload.query.strip():
        raise HTTPException(status_code=422, detail="Query cannot be empty")

    # Hard overage ceiling check
    if await _check_overage_ceiling(db, workspace_id):
        raise HTTPException(
            status_code=402,
            detail="Query paused: overage ceiling reached for this billing period. Please upgrade your plan.",
        )

    is_overage = await _check_overage(db, workspace_id)

    # Embed query (local model, no cost)
    import asyncio
    query_vec = await asyncio.get_event_loop().run_in_executor(
        None, embedding_service.embed_query, payload.query
    )

    # Hybrid retrieval
    results = await retrieval_service.hybrid_search(
        db,
        workspace_id=workspace_id,
        query_embedding=query_vec,
        query_text=payload.query,
    )

    # Query synthesis
    lang = payload.language if payload.language in ("en", "es") else current_user.preferred_language
    answer, sources, usage = await query_synthesis_service.synthesize(
        payload.query, results, language=lang
    )

    cost = Decimal(str(
        settings._COST_RATES_SYNTHESIS_INPUT * usage["input_tokens"] +
        settings._COST_RATES_SYNTHESIS_OUTPUT * usage["output_tokens"]
    )) if hasattr(settings, "_COST_RATES_SYNTHESIS_INPUT") else Decimal("0")

    # Approximate cost using Sonnet pricing
    cost = Decimal(str(0.000003 * usage["input_tokens"] + 0.000015 * usage["output_tokens"]))

    event = await log_usage_event(
        db,
        workspace_id=workspace_id,
        task_type="query_synthesis",
        provider_model=settings.SYNTHESIS_MODEL,
        input_units=usage["input_tokens"],
        output_units=usage["output_tokens"],
        cost_usd=cost,
        is_overage=is_overage,
    )

    # If overage, update running overage total on subscription
    if is_overage:
        sub_result = await db.execute(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        )
        sub = sub_result.scalar_one_or_none()
        if sub:
            sub.current_period_overage_usd += cost * Decimal(str(sub.overage_rate_multiplier))

    await db.commit()

    return AskResponse(answer=answer, sources=sources)
