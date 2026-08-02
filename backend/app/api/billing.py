"""
Billing API — subscription management, Stripe Checkout/Portal, webhook receiver.
No raw card data ever passes through here.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent
from app.services.billing_service import (
    PLANS,
    sync_budget_cap,
    get_period_spend,
    create_checkout_session,
    create_portal_session,
    handle_stripe_webhook,
)

router = APIRouter(tags=["billing"])


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def _assert_member(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    r = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if not r.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


async def _assert_owner(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID):
    r = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_user_id == user_id)
    )
    if not r.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Only the workspace owner can manage billing")


# ── Response schemas ──────────────────────────────────────────────────────────

class PlanInfo(BaseModel):
    plan_name: str
    price_usd_monthly: Decimal
    included_budget_usd: Decimal
    included_seats: int
    price_per_seat_usd: Decimal
    included_budget_per_seat_usd: Decimal
    overage_rate_multiplier: Decimal
    overage_ceiling_usd: Decimal


class BillingStatus(BaseModel):
    has_subscription: bool
    status: str                          # inactive | active | past_due | canceled
    plan: PlanInfo | None
    current_period_end: datetime | None
    period_spend_usd: Decimal            # non-overage spend this period
    budget_cap_usd: Decimal | None       # workspace.monthly_budget_cap
    budget_pct: float | None             # period_spend / budget_cap, 0–1
    current_period_overage_usd: Decimal
    overage_ceiling_usd: Decimal | None
    overage_pct: float | None
    has_portal: bool                     # True if customer_id exists → Portal available
    payment_enabled: bool


class UsageBreakdown(BaseModel):
    task_type: str
    cost_usd: Decimal
    event_count: int


class BillingDetails(BaseModel):
    status: BillingStatus
    breakdown: list[UsageBreakdown]     # current period spend by task_type


class SessionUrl(BaseModel):
    url: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/workspaces/{workspace_id}/billing", response_model=BillingDetails)
async def get_billing(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return subscription status, period spend, and cost breakdown for the current period."""
    await _assert_member(db, workspace_id, current_user.id)

    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    sub_result = await db.execute(select(Subscription).where(Subscription.workspace_id == workspace_id))
    sub = sub_result.scalar_one_or_none()

    period_spend = Decimal("0")
    if sub:
        period_spend = await get_period_spend(db, workspace_id, sub)

    budget_cap = ws.monthly_budget_cap
    budget_pct = float(period_spend / budget_cap) if budget_cap and budget_cap > 0 else None
    overage = sub.current_period_overage_usd if sub else Decimal("0")
    ceiling = sub.overage_ceiling_usd if sub else None
    overage_pct = float(overage / ceiling) if ceiling and ceiling > 0 else None

    plan_info: PlanInfo | None = None
    if sub:
        plan_info = PlanInfo(
            plan_name=sub.plan_name,
            price_usd_monthly=sub.price_usd_monthly,
            included_budget_usd=sub.included_budget_usd,
            included_seats=sub.included_seats,
            price_per_seat_usd=sub.price_per_seat_usd,
            included_budget_per_seat_usd=sub.included_budget_per_seat_usd,
            overage_rate_multiplier=sub.overage_rate_multiplier,
            overage_ceiling_usd=sub.overage_ceiling_usd,
        )

    billing_status = BillingStatus(
        has_subscription=sub is not None,
        status=sub.status if sub else "inactive",
        plan=plan_info,
        current_period_end=sub.current_period_end if sub else None,
        period_spend_usd=period_spend,
        budget_cap_usd=budget_cap,
        budget_pct=budget_pct,
        current_period_overage_usd=overage,
        overage_ceiling_usd=ceiling,
        overage_pct=overage_pct,
        has_portal=bool(sub and sub.payment_provider_customer_id),
        payment_enabled=ws.payment_enabled,
    )

    # Cost breakdown by task_type for current period
    period_start = sub.current_period_start if sub else None
    q = (
        select(
            UsageEvent.task_type,
            func.sum(UsageEvent.cost_usd).label("cost_usd"),
            func.count().label("event_count"),
        )
        .where(UsageEvent.workspace_id == workspace_id)
        .group_by(UsageEvent.task_type)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
    )
    if period_start:
        q = q.where(UsageEvent.created_at >= period_start)

    rows = await db.execute(q)
    breakdown = [
        UsageBreakdown(task_type=r.task_type, cost_usd=Decimal(str(r.cost_usd)), event_count=r.event_count)
        for r in rows.all()
    ]

    return BillingDetails(status=billing_status, breakdown=breakdown)


@router.post("/workspaces/{workspace_id}/billing/checkout", response_model=SessionUrl)
async def start_checkout(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout session. Returns the redirect URL to Stripe's payment page."""
    await _assert_owner(db, workspace_id, current_user.id)

    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404)

    try:
        url = await create_checkout_session(
            db,
            workspace_id=workspace_id,
            owner_email=current_user.email,
            workspace_name=ws.name,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    await db.commit()
    return SessionUrl(url=url)


@router.post("/workspaces/{workspace_id}/billing/portal", response_model=SessionUrl)
async def open_portal(
    workspace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Billing Portal session to manage subscription/payment method."""
    await _assert_owner(db, workspace_id, current_user.id)

    sub_result = await db.execute(select(Subscription).where(Subscription.workspace_id == workspace_id))
    sub = sub_result.scalar_one_or_none()
    if not sub or not sub.payment_provider_customer_id:
        raise HTTPException(status_code=404, detail="No active subscription found")

    try:
        url = await create_portal_session(sub.payment_provider_customer_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return SessionUrl(url=url)


@router.post("/billing/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Stripe webhook receiver. Keeps subscription.status in sync automatically.
    A super admin's manual override always takes precedence over webhook-driven status.
    Registered in Stripe Dashboard → Developers → Webhooks.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        result = await handle_stripe_webhook(db, payload, sig_header)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


@router.get("/billing/plans", response_model=list[PlanInfo])
async def list_plans(current_user: User = Depends(get_current_user)):
    """Return available subscription plans (read-only, no auth other than logged in)."""
    return [
        PlanInfo(**{k: v for k, v in plan.items() if k != "plan_name"}, plan_name=plan["plan_name"])
        for plan in PLANS.values()
    ]
