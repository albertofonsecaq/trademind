"""
Ask endpoint — the core query interface.
Budget/overage check runs before synthesis to enforce spend controls.
query_synthesis usage is tagged is_overage=True once included_budget is exhausted.

An answered question is kept as an ask record, so the page survives a reload
and a question already paid for can be read again instead of asked again
(#72, #73). Reading one back is a read and nothing else — no retrieval, no
synthesis, no usage event. Records are personal: a user sees only their own,
and deletes only their own (#74).
"""
import uuid
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, delete
from sqlalchemy.sql.elements import ColumnElement

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember  # noqa: F401 (used in _get_workspace_and_assert_member)
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent
from app.models.ask_record import AskRecord
from app.models.evidence_image import EvidenceImage
from app.models.evidence_item import EvidenceItem
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


class AskRecordCount(BaseModel):
    count: int


class AskRecordsCleared(BaseModel):
    deleted: int


class AskRecordOut(AskRecordSummary):
    """One ask record in full: what the list row leaves in the database."""
    model_config = {"from_attributes": True}
    answer: str
    sources: list[dict]
    language: str


async def _beside_their_evidence(db: AsyncSession, workspace_id: uuid.UUID, sources: list[dict]) -> list[dict]:
    """
    The cited sources, each saying which evidence it is and whether that has a
    picture to show (#110).

    Every source has always been kept with its evidence's stable id, so this is
    a lookup, made when an answer is served: an answer asked before pictures
    were kept (#108) shows them too, and what is stored never changes. A source
    whose evidence is not in this workspace any more names none.
    """
    stable_ids = [s.get("stable_id") for s in sources if s.get("stable_id")]
    found: dict[str, tuple[str, bool]] = {}
    if stable_ids:
        rows = await db.execute(
            select(EvidenceItem.stable_id, EvidenceItem.id, EvidenceImage.id)
            .outerjoin(EvidenceImage, EvidenceImage.evidence_item_id == EvidenceItem.id)
            .where(EvidenceItem.workspace_id == workspace_id, EvidenceItem.stable_id.in_(stable_ids))
        )
        found = {stable_id: (str(item_id), image_id is not None) for stable_id, item_id, image_id in rows}
    served = []
    for source in sources:
        evidence_id, has_image = found.get(source.get("stable_id"), (None, False))
        served.append({**source, "evidence_id": evidence_id, "has_image": has_image})
    return served


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


def _mine(user: User, workspace_id: uuid.UUID) -> tuple[ColumnElement[bool], ...]:
    """
    The clause that makes an ask record the caller's own.

    Every route that reads, pages through or deletes one filters on it —
    including the lookup of a paging anchor, which decides nothing on its own
    and would still leak that somebody else's record exists. Written once
    because an ask record is a personal layer, and a route that forgot half of
    this would hand one user another's questions.
    """
    return (AskRecord.user_id == user.id, AskRecord.workspace_id == workspace_id)


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
        # Before the commit: a lookup that failed after it would leave an answer
        # kept that the caller never received. Inside the try, because the
        # synthesis it follows is owed a usage event however this ends (#27).
        served_sources = await _beside_their_evidence(db, workspace_id, sources)
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
        answer=answer, sources=served_sources, id=record.id, created_at=record.created_at,
    )


@router.get("/{workspace_id}/ask-records", response_model=list[AskRecordSummary])
async def list_ask_records(
    workspace_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=200),
    before_id: uuid.UUID | None = None,
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

    `before_id` asks for the page older than that record — the caller passes the
    oldest row it already has. The anchor is the record itself rather than a
    row offset, so a question asked while someone reads back through their
    history cannot shift the pages underneath them and make a row appear twice
    or not at all.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    mine = _mine(current_user, workspace_id)
    query = select(AskRecord.id, AskRecord.question, AskRecord.created_at).where(*mine)

    if before_id is not None:
        anchor = await db.execute(
            select(AskRecord.created_at).where(AskRecord.id == before_id, *mine)
        )
        anchored_at = anchor.scalar_one_or_none()
        if anchored_at is None:
            # Same reasoning as reading one: a caller who does not own this id
            # does not get to learn whether it exists.
            raise HTTPException(status_code=404, detail="Ask record not found")
        query = query.where(
            or_(
                AskRecord.created_at < anchored_at,
                and_(AskRecord.created_at == anchored_at, AskRecord.id < before_id),
            )
        )

    result = await db.execute(
        # The id breaks a tie, so "newest first" is one order rather than
        # whichever of two records written in the same microsecond came back —
        # and it is what makes the page boundary above exact.
        query.order_by(AskRecord.created_at.desc(), AskRecord.id.desc()).limit(limit)
    )
    return [
        AskRecordSummary(id=row.id, question=row.question, created_at=row.created_at)
        for row in result.all()
    ]


@router.get("/{workspace_id}/ask-records/count", response_model=AskRecordCount)
async def count_ask_records(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    How many ask records the caller has here.

    Declared above the `{record_id}` route on purpose: FastAPI matches in
    declaration order, and below it "count" would be read as a record id and
    rejected as malformed.

    It exists so the confirm before clearing can say how many records it is
    about to take. The page knows how much of the history it has loaded, which
    is not the same number.

    This and the delete that follows it are two requests, so a question asked
    in between makes the confirmed number low by one. The delete takes what is
    actually there and reports it, so nothing is missed — only the sentence the
    user read was out of date, and by less than they took to read it.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    total = await db.execute(
        select(func.count(AskRecord.id)).where(*_mine(current_user, workspace_id))
    )
    return AskRecordCount(count=total.scalar() or 0)


@router.delete("/{workspace_id}/ask-records", response_model=AskRecordsCleared)
async def clear_ask_records(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every ask record the caller has here, gone, and says how many that was.

    The one operation on this table that names no id, so the two filters are
    the whole of what stands between a user's own history and the one next to
    it. One statement rather than a read and a loop: nothing can be added
    between deciding what to delete and deleting it.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    result = await db.execute(delete(AskRecord).where(*_mine(current_user, workspace_id)))
    await db.commit()
    return AskRecordsCleared(deleted=result.rowcount or 0)


@router.delete("/{workspace_id}/ask-records/{record_id}", status_code=204)
async def delete_ask_record(
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    One of the caller's own ask records, gone.

    Idempotent: deleting a record that is not there succeeds, and so does
    deleting one that belongs to somebody else. A delete states the end state
    it wants — that record, not in this user's history — and that state is
    already true in both cases, so there is nothing to report. A double click
    is the first case, and is not a problem the user has to understand.

    `plan_items.remove_plan_item` reads the same way, deliberately: these are
    the two personal-layer deletes in this API and they used to disagree. A 404
    would have been just as silent — it would be the same 404 for a record that
    never existed as for one that is not yours, which is what reading one does
    — so the choice between them was idempotency, and both now make it.

    The record goes and nothing else does. The usage event for the call that
    produced the answer stays, because the money was spent and is still owed;
    so do the evidence it cited and every strategy card, because the knowledge
    base is not the user's transcript.
    """
    await _get_workspace_and_assert_member(db, workspace_id, current_user.id)

    await db.execute(
        delete(AskRecord).where(AskRecord.id == record_id, *_mine(current_user, workspace_id))
    )
    await db.commit()


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
        select(AskRecord).where(AskRecord.id == record_id, *_mine(current_user, workspace_id))
    )
    record = result.scalar_one_or_none()
    if not record:
        # Not a 403. Whether this id exists at all is not something a caller who
        # does not own it gets to learn, and 403 would answer exactly that.
        raise HTTPException(status_code=404, detail="Ask record not found")
    # As stored, with whether each source has a picture looked up beside it.
    return AskRecordOut(
        id=record.id, question=record.question, created_at=record.created_at,
        answer=record.answer, language=record.language,
        sources=await _beside_their_evidence(db, workspace_id, record.sources),
    )
