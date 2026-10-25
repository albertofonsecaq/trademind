"""
Ask endpoint — the core query interface.
Budget/overage check runs before synthesis to enforce spend controls.
query_synthesis usage is tagged is_overage=True once included_budget is exhausted.

An answered question is kept as an ask record, so the transcript survives a
reload and a question already paid for can be read again instead of asked
again (#72). Records are personal: a user sees only their own.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember  # noqa: F401 (used in _get_workspace_and_assert_member)
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent
from app.models.ask_record import AskRecord
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
    # The record this answer was kept as, so a client can show a fresh answer
    # and one read back from the history as the same thing.
    id: uuid.UUID
    created_at: datetime


class AskRecordSummary(BaseModel):
    """
    One row of the list: enough to recognise a question, and not the answer.

    The answers are what makes this table big, and only the one a user opens is
    needed — so they are not read at all until then.
    """
    id: uuid.UUID
    question: str
    created_at: datetime


class AskRecordOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    question: str
    answer: str
    sources: list[dict]
    language: str
    created_at: datetime


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

    question = payload.query.strip()
    if not question:
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
        None, embedding_service.embed_query, question
    )

    # Hybrid retrieval
    results = await retrieval_service.hybrid_search(
        db,
        workspace_id=workspace_id,
        query_embedding=query_vec,
        query_text=question,
    )

    # Query synthesis
    lang = payload.language if payload.language in ("en", "es") else current_user.preferred_language
    answer, sources, usage = await query_synthesis_service.synthesize(
        question, results, language=lang
    )

    cost = Decimal(str(
        settings._COST_RATES_SYNTHESIS_INPUT * usage["input_tokens"] +
        settings._COST_RATES_SYNTHESIS_OUTPUT * usage["output_tokens"]
    )) if hasattr(settings, "_COST_RATES_SYNTHESIS_INPUT") else Decimal("0")

    # Approximate cost using Sonnet pricing
    cost = Decimal(str(0.000003 * usage["input_tokens"] + 0.000015 * usage["output_tokens"]))

    # Keep the answer. In this request's transaction, unlike the usage event
    # below: an ask record is the answer itself, so if the request does not
    # survive to commit there is no answer to have kept. Flushed here so the
    # response can name the record it was stored as.
    record = AskRecord(
        workspace_id=workspace_id,
        user_id=current_user.id,
        question=question,
        answer=answer,
        sources=sources,
        language=lang,
    )
    db.add(record)
    await db.flush()

    # If overage, update running overage total on subscription
    if is_overage:
        sub_result = await db.execute(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        )
        sub = sub_result.scalar_one_or_none()
        if sub:
            sub.current_period_overage_usd += cost * Decimal(str(sub.overage_rate_multiplier))

    try:
        await db.commit()
    finally:
        # In its own transaction, after this one, and in a `finally` because the
        # call happened either way. It used to share this request's transaction
        # and could roll back with it: tokens spent, the provider billing for
        # them, nothing recorded (#27). After the commit, so an overage row
        # cannot outlive a request that failed — and still written when the
        # commit itself fails, which keeping the answer here made possible (the
        # record is one more way this transaction can go wrong). A failed commit
        # leaves the subscription's running overage unincremented, so that case
        # under-meters by this call rather than losing it entirely.
        await log_usage_event(
            workspace_id=workspace_id,
            task_type="query_synthesis",
            provider_model=settings.SYNTHESIS_MODEL,
            input_units=usage["input_tokens"],
            output_units=usage["output_tokens"],
            cost_usd=cost,
            is_overage=is_overage,
        )

    return AskResponse(
        answer=answer, sources=sources, id=record.id, created_at=record.created_at
    )


@router.get("/{workspace_id}/ask-records", response_model=list[AskRecordSummary])
async def list_ask_records(
    workspace_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Which questions the calling user asked in this workspace, newest first.

    Scoped to the caller, not to the workspace's membership: an ask record is a
    personal layer, so being a member of the workspace a question was asked in
    is not being allowed to read the question.

    Three columns, not the row: a user with hundreds of records should not wait
    for hundreds of answers to come back so a list of questions can be drawn.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    result = await db.execute(
        select(AskRecord.id, AskRecord.question, AskRecord.created_at)
        .where(
            AskRecord.user_id == current_user.id,
            AskRecord.workspace_id == workspace_id,
        )
        # The id breaks a tie, so "newest first" is one order rather than
        # whichever of two records written in the same microsecond came back.
        .order_by(AskRecord.created_at.desc(), AskRecord.id.desc())
        .limit(limit)
    )
    return [
        AskRecordSummary(id=row.id, question=row.question, created_at=row.created_at)
        for row in result.all()
    ]


@router.get("/{workspace_id}/ask-records/{record_id}", response_model=AskRecordOut)
async def read_ask_record(
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    One of the caller's own ask records, as it was stored.

    This is a read and nothing else. No retrieval, no synthesis, no usage
    event, nothing against the included budget or the overage ceiling: the
    answer was paid for once, when it was asked, and that is the whole reason
    it was kept. Re-synthesizing would not even return the same answer.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    result = await db.execute(
        select(AskRecord).where(
            AskRecord.id == record_id,
            AskRecord.user_id == current_user.id,
            AskRecord.workspace_id == workspace_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        # Not a 403. Whether this id exists at all is not something a caller who
        # does not own it gets to learn, and 403 would answer exactly that.
        raise HTTPException(status_code=404, detail="Ask record not found")
    return record
