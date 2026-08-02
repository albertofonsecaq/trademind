"""
Billing service — plan catalog, budget-cap sync, and Stripe integration.

Design constraints enforced here:
- Plan pricing is DATA (PLANS dict), not hardcoded in API logic.
- workspace.monthly_budget_cap is ALWAYS derived from the active subscription's
  included_budget_usd + seat adjustments. It is never set manually while a
  subscription is active — the margin guarantee is structural.
- Seat changes trigger a Stripe subscription quantity update so the next invoice
  reflects the real member count automatically.
- Overage is metered via Stripe InvoiceItem created at period-end webhook.
- Raw card data never touches this service — all card flows go through Stripe Checkout.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent

log = logging.getLogger(__name__)

# ── Plan catalog ──────────────────────────────────────────────────────────────
# Stored as data — update values here, no API changes needed.
# When a subscription is created, these values are copied into the Subscription row
# so historical subscriptions retain the price they were charged at.

PLANS: dict[str, dict] = {
    "standard": {
        "plan_name": "standard",
        "price_usd_monthly": Decimal("50.00"),
        "included_budget_usd": Decimal("30.00"),
        "included_seats": 1,
        "price_per_seat_usd": Decimal("10.00"),
        "included_budget_per_seat_usd": Decimal("8.00"),
        "overage_rate_multiplier": Decimal("1.5"),
        "overage_ceiling_usd": Decimal("50.00"),
    }
}


def get_plan(plan_name: str) -> dict:
    plan = PLANS.get(plan_name)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_name!r}")
    return plan


# ── Budget cap sync ───────────────────────────────────────────────────────────

async def sync_budget_cap(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """
    Recompute workspace.monthly_budget_cap from the active subscription + member count.
    Called whenever subscription status changes or a member is added/removed.
    Only active or past_due subscriptions drive the cap; inactive/canceled → no change.
    """
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()

    if not sub or not ws:
        return

    if sub.status not in ("active", "past_due"):
        return  # Subscription lapsed — leave manual cap as-is

    member_count_result = await db.execute(
        select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    member_count = member_count_result.scalar() or 1
    extra_seats = max(0, member_count - sub.included_seats)

    budget = sub.included_budget_usd + extra_seats * sub.included_budget_per_seat_usd
    ws.monthly_budget_cap = budget
    await db.flush()
    log.info("Budget cap synced for workspace %s: $%.2f (members=%d)", workspace_id, budget, member_count)


async def get_period_spend(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    sub: Subscription,
) -> Decimal:
    """Sum non-overage usage_events within the current billing period."""
    if not sub.current_period_start:
        return Decimal("0")
    result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
            UsageEvent.workspace_id == workspace_id,
            UsageEvent.is_overage == False,
            UsageEvent.created_at >= sub.current_period_start,
        )
    )
    return Decimal(str(result.scalar()))


async def is_budget_exhausted(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """True if the workspace has spent its full included budget this period."""
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if not sub or sub.status != "active" or not sub.current_period_start:
        return False
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws or not ws.monthly_budget_cap:
        return False
    spent = await get_period_spend(db, workspace_id, sub)
    return spent >= ws.monthly_budget_cap


async def is_payment_lapsed(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """
    True if the workspace has payment enforcement active but no valid subscription.
    Used by the poller to gate deferrable background jobs.
    """
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws or not ws.payment_enabled:
        return False  # payment not enforced → always allowed
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if not sub:
        return True
    # super_admin manual override takes precedence
    if sub.manually_overridden_by is not None:
        return sub.status not in ("active", "past_due")
    return sub.status not in ("active", "past_due")


# ── Stripe integration ────────────────────────────────────────────────────────

def _stripe():
    """Return configured stripe module, or raise if key not set."""
    import stripe as _s
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not configured. Set it in your server environment."
        )
    _s.api_key = settings.STRIPE_SECRET_KEY
    return _s


async def get_or_create_stripe_customer(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    owner_email: str,
    workspace_name: str,
) -> str:
    """Return existing Stripe customer_id or create a new one."""
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()

    if sub and sub.payment_provider_customer_id:
        return sub.payment_provider_customer_id

    stripe = _stripe()
    customer = stripe.Customer.create(
        email=owner_email,
        name=workspace_name,
        metadata={"workspace_id": str(workspace_id)},
    )
    customer_id = customer["id"]

    if sub:
        sub.payment_provider_customer_id = customer_id
        await db.flush()

    return customer_id


async def create_checkout_session(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    owner_email: str,
    workspace_name: str,
    plan_name: str = "standard",
) -> str:
    """
    Create a Stripe Checkout session and return the redirect URL.
    No card data passes through our server — Stripe hosts the payment page.
    """
    if not settings.STRIPE_STANDARD_PRICE_ID:
        raise RuntimeError(
            "STRIPE_STANDARD_PRICE_ID is not configured. "
            "Create a recurring price in your Stripe Dashboard and set this env var."
        )
    stripe = _stripe()
    customer_id = await get_or_create_stripe_customer(
        db, workspace_id, owner_email, workspace_name
    )
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": settings.STRIPE_STANDARD_PRICE_ID, "quantity": 1}],
        success_url=f"{settings.FRONTEND_URL}/billing?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.FRONTEND_URL}/billing",
        metadata={"workspace_id": str(workspace_id), "plan_name": plan_name},
        subscription_data={"metadata": {"workspace_id": str(workspace_id)}},
    )
    return session["url"]


async def create_portal_session(customer_id: str) -> str:
    """Create a Stripe Billing Portal session and return the redirect URL."""
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{settings.FRONTEND_URL}/billing",
    )
    return session["url"]


async def handle_stripe_webhook(
    db: AsyncSession,
    payload: bytes,
    sig_header: str,
) -> dict:
    """
    Process an incoming Stripe webhook event.
    Verifies signature, then dispatches to the appropriate handler.
    Returns {"handled": True/False, "event_type": str}.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        log.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        import json
        event = json.loads(payload)
    else:
        stripe = _stripe()
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            raise ValueError(f"Webhook signature verification failed: {e}") from e

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        await _handle_subscription_upsert(db, data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, data)
    elif event_type == "invoice.payment_succeeded":
        await _handle_invoice_paid(db, data)
    elif event_type == "invoice.payment_failed":
        await _handle_invoice_failed(db, data)
    elif event_type == "checkout.session.completed":
        # subscription.created fires separately; nothing extra needed here
        pass
    else:
        return {"handled": False, "event_type": event_type}

    await db.commit()
    return {"handled": True, "event_type": event_type}


async def _handle_subscription_upsert(db: AsyncSession, data: dict) -> None:
    workspace_id_str = (data.get("metadata") or {}).get("workspace_id")
    if not workspace_id_str:
        return
    workspace_id = uuid.UUID(workspace_id_str)

    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()

    stripe_status = data.get("status", "inactive")
    our_status = _map_stripe_status(stripe_status)

    period_start = _ts(data.get("current_period_start"))
    period_end = _ts(data.get("current_period_end"))

    # Extract subscription item ID (base plan item) for future quantity updates
    items = (data.get("items") or {}).get("data") or []
    sub_item_id = items[0]["id"] if items else None

    if sub:
        # Only update status if no manual override is active
        if sub.manually_overridden_by is None:
            sub.status = our_status
        sub.payment_provider_subscription_id = data.get("id")
        sub.current_period_start = period_start
        sub.current_period_end = period_end
        if sub_item_id:
            sub.stripe_subscription_item_id = sub_item_id
    else:
        # First-time subscription — create with plan defaults
        plan = get_plan("standard")
        sub = Subscription(
            workspace_id=workspace_id,
            plan_name=plan["plan_name"],
            price_usd_monthly=plan["price_usd_monthly"],
            included_budget_usd=plan["included_budget_usd"],
            included_seats=plan["included_seats"],
            price_per_seat_usd=plan["price_per_seat_usd"],
            included_budget_per_seat_usd=plan["included_budget_per_seat_usd"],
            overage_rate_multiplier=plan["overage_rate_multiplier"],
            overage_ceiling_usd=plan["overage_ceiling_usd"],
            payment_provider_subscription_id=data.get("id"),
            status=our_status,
            current_period_start=period_start,
            current_period_end=period_end,
            stripe_subscription_item_id=sub_item_id,
        )
        db.add(sub)

    await db.flush()
    await sync_budget_cap(db, workspace_id)


async def _handle_subscription_deleted(db: AsyncSession, data: dict) -> None:
    workspace_id_str = (data.get("metadata") or {}).get("workspace_id")
    if not workspace_id_str:
        return
    workspace_id = uuid.UUID(workspace_id_str)
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if sub and sub.manually_overridden_by is None:
        sub.status = "canceled"
        await db.flush()


async def _handle_invoice_paid(db: AsyncSession, data: dict) -> None:
    """
    Payment succeeded: reset per-period overage counter, add prior-period overage
    as an invoice item if any accrued, update period dates.
    """
    subscription_id = data.get("subscription")
    if not subscription_id:
        return

    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.payment_provider_subscription_id == subscription_id
        )
    )
    sub = sub_result.scalar_one_or_none()
    if not sub:
        return

    # Bill any accrued overage as a one-time invoice item on the *next* invoice
    if sub.current_period_overage_usd > 0 and sub.payment_provider_customer_id:
        try:
            stripe = _stripe()
            overage_cents = int(sub.current_period_overage_usd * 100)
            stripe.InvoiceItem.create(
                customer=sub.payment_provider_customer_id,
                amount=overage_cents,
                currency="usd",
                description=f"Compute overage ({sub.current_period_overage_usd:.4f} USD × {sub.overage_rate_multiplier}x)",
            )
        except Exception as e:
            log.error("Failed to create overage invoice item: %s", e)

    # Reset overage counter for the new period
    sub.current_period_overage_usd = Decimal("0")

    # Update period dates from the subscription (Stripe sends next period in the invoice)
    # The invoice period_end is the new period's end for next cycle
    period_start = _ts(data.get("period_start") or data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("start"))
    period_end = _ts(data.get("period_end") or data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end"))
    if period_start:
        sub.current_period_start = period_start
    if period_end:
        sub.current_period_end = period_end

    if sub.manually_overridden_by is None:
        sub.status = "active"

    await db.flush()
    await sync_budget_cap(db, sub.workspace_id)


async def _handle_invoice_failed(db: AsyncSession, data: dict) -> None:
    subscription_id = data.get("subscription")
    if not subscription_id:
        return
    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.payment_provider_subscription_id == subscription_id
        )
    )
    sub = sub_result.scalar_one_or_none()
    if sub and sub.manually_overridden_by is None:
        sub.status = "past_due"
        await db.flush()


def _map_stripe_status(stripe_status: str) -> str:
    return {
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "past_due",
        "incomplete": "inactive",
        "incomplete_expired": "inactive",
        "trialing": "active",
        "paused": "past_due",
    }.get(stripe_status, "inactive")


def _ts(unix: int | None) -> datetime | None:
    if not unix:
        return None
    return datetime.fromtimestamp(int(unix), tz=timezone.utc)


# ── Seat update ───────────────────────────────────────────────────────────────

async def update_seat_quantity(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    new_member_count: int,
) -> None:
    """
    Called when a member is added or removed. Updates Stripe subscription quantity
    to reflect extra seats beyond included_seats. Also re-syncs budget cap.
    """
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if not sub or sub.status not in ("active",) or not sub.stripe_subscription_item_id:
        await sync_budget_cap(db, workspace_id)
        return

    extra_seats = max(0, new_member_count - sub.included_seats)
    try:
        stripe = _stripe()
        stripe.SubscriptionItem.modify(
            sub.stripe_subscription_item_id,
            quantity=max(1, extra_seats + 1),  # +1: base seat always included
        )
    except Exception as e:
        log.error("Failed to update Stripe seat quantity for workspace %s: %s", workspace_id, e)

    await sync_budget_cap(db, workspace_id)
