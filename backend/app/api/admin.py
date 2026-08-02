"""
Super admin API — platform-level back-office, only for users with platform_role="super_admin".
Every action is audited in admin_audit_logs.

Distinct from the per-workspace Admin/config view. Super admins can:
  - List all workspaces with subscription + spend data
  - Create a workspace on behalf of any user
  - Add or remove workspace members (bypassing normal invite flow)
  - Toggle workspace.payment_enabled (overrides Stripe subscription status)
  - View aggregate profitability (revenue vs compute cost vs margin)
  - Read the audit log
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.models.subscription import Subscription
from app.models.usage_event import UsageEvent
from app.models.admin_audit_log import AdminAuditLog
from app.services.billing_service import sync_budget_cap, update_seat_quantity
from app.services.workspace_service import create_private_workspace

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Super admin guard ──────────────────────────────────────────────────────────

def _require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.platform_role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


async def _audit(
    db: AsyncSession,
    admin_id: uuid.UUID,
    action: str,
    workspace_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    details: dict | None = None,
):
    log = AdminAuditLog(
        admin_user_id=admin_id,
        action=action,
        target_workspace_id=workspace_id,
        target_user_id=target_user_id,
        details=details or {},
    )
    db.add(log)
    await db.flush()


# ── Schemas ───────────────────────────────────────────────────────────────────

class WorkspaceAdminView(BaseModel):
    id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID
    owner_email: str | None
    payment_enabled: bool
    subscription_status: str
    plan_name: str | None
    price_usd_monthly: Decimal | None
    period_spend_usd: Decimal
    monthly_budget_cap: Decimal | None
    member_count: int
    created_at: datetime


class ProfitabilityView(BaseModel):
    total_revenue_usd: Decimal
    total_compute_cost_usd: Decimal
    realized_margin_usd: Decimal
    margin_pct: float | None
    workspace_count: int
    active_subscriptions: int
    at_risk_workspaces: list[dict]   # workspaces where cost > 80% of included_budget


class AuditEntry(BaseModel):
    id: uuid.UUID
    admin_user_id: uuid.UUID
    action: str
    target_workspace_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    details: dict
    created_at: datetime


class CreateWorkspaceRequest(BaseModel):
    owner_email: str
    name: str


class AddMemberRequest(BaseModel):
    user_email: str
    role: str = "member"


class TogglePaymentRequest(BaseModel):
    payment_enabled: bool


# ── Workspace list ─────────────────────────────────────────────────────────────

@router.get("/workspaces", response_model=list[WorkspaceAdminView])
async def list_all_workspaces(
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Workspace)
        .options(selectinload(Workspace.subscription))
        .order_by(Workspace.created_at.desc())
    )
    workspaces = rows.scalars().all()

    result = []
    for ws in workspaces:
        sub = ws.subscription

        # Owner email
        owner_result = await db.execute(
            select(User.email).where(User.id == ws.owner_user_id)
        )
        owner_email = owner_result.scalar_one_or_none()

        # Member count
        mc_result = await db.execute(
            select(func.count()).select_from(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws.id
            )
        )
        member_count = mc_result.scalar() or 0

        # Period spend
        period_spend = Decimal("0")
        if sub and sub.current_period_start:
            ps_result = await db.execute(
                select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                    UsageEvent.workspace_id == ws.id,
                    UsageEvent.is_overage == False,
                    UsageEvent.created_at >= sub.current_period_start,
                )
            )
            period_spend = Decimal(str(ps_result.scalar()))

        result.append(WorkspaceAdminView(
            id=ws.id,
            name=ws.name,
            owner_user_id=ws.owner_user_id,
            owner_email=owner_email,
            payment_enabled=ws.payment_enabled,
            subscription_status=sub.status if sub else "inactive",
            plan_name=sub.plan_name if sub else None,
            price_usd_monthly=sub.price_usd_monthly if sub else None,
            period_spend_usd=period_spend,
            monthly_budget_cap=ws.monthly_budget_cap,
            member_count=member_count,
            created_at=ws.created_at,
        ))
    return result


# ── Create workspace ───────────────────────────────────────────────────────────

@router.post("/workspaces", response_model=WorkspaceAdminView, status_code=201)
async def create_workspace_for_user(
    payload: CreateWorkspaceRequest,
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(select(User).where(User.email == payload.owner_email))
    owner = user_result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail=f"User {payload.owner_email!r} not found")

    ws = await create_private_workspace(db, owner_id=owner.id, name=payload.name)
    await _audit(db, admin.id, "create_workspace", workspace_id=ws.id,
                 details={"owner_email": payload.owner_email, "name": payload.name})
    await db.commit()

    return WorkspaceAdminView(
        id=ws.id, name=ws.name, owner_user_id=ws.owner_user_id, owner_email=owner.email,
        payment_enabled=ws.payment_enabled, subscription_status="inactive",
        plan_name=None, price_usd_monthly=None, period_spend_usd=Decimal("0"),
        monthly_budget_cap=ws.monthly_budget_cap, member_count=1, created_at=ws.created_at,
    )


# ── Manage members ─────────────────────────────────────────────────────────────

@router.post("/workspaces/{workspace_id}/members", status_code=201)
async def add_member(
    workspace_id: uuid.UUID,
    payload: AddMemberRequest,
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a workspace, bypassing the normal invite flow."""
    user_result = await db.execute(select(User).where(User.email == payload.user_email))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {payload.user_email!r} not found")

    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Check not already a member
    existing = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member of this workspace")

    member = WorkspaceMember(
        workspace_id=workspace_id, user_id=user.id,
        role=payload.role if payload.role in ("owner", "member") else "member",
        joined_at=datetime.now(timezone.utc),
    )
    db.add(member)
    await db.flush()

    # Update seat pricing on Stripe
    mc_result = await db.execute(
        select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    member_count = mc_result.scalar() or 1
    await update_seat_quantity(db, workspace_id, member_count)

    await _audit(db, admin.id, "add_member", workspace_id=workspace_id,
                 target_user_id=user.id, details={"email": payload.user_email, "role": payload.role})
    await db.commit()
    return {"workspace_id": str(workspace_id), "user_id": str(user.id), "role": member.role}


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a workspace."""
    # Prevent removing the owner
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404)
    if ws.owner_user_id == user_id:
        raise HTTPException(status_code=409, detail="Cannot remove the workspace owner")

    member_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    await db.delete(member)
    await db.flush()

    mc_result = await db.execute(
        select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    member_count = mc_result.scalar() or 1
    await update_seat_quantity(db, workspace_id, member_count)

    await _audit(db, admin.id, "remove_member", workspace_id=workspace_id,
                 target_user_id=user_id)
    await db.commit()


# ── Payment override ───────────────────────────────────────────────────────────

@router.patch("/workspaces/{workspace_id}/payment")
async def toggle_payment(
    workspace_id: uuid.UUID,
    payload: TogglePaymentRequest,
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually activate or deactivate payment enforcement for a workspace.
    Independent of the Stripe subscription status — e.g. to comp an account
    or override a billing edge case.
    """
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404)

    old_value = ws.payment_enabled
    ws.payment_enabled = payload.payment_enabled

    # Also mark the subscription as manually overridden
    sub_result = await db.execute(
        select(Subscription).where(Subscription.workspace_id == workspace_id)
    )
    sub = sub_result.scalar_one_or_none()
    if sub:
        sub.manually_overridden_by = admin.id

    await _audit(db, admin.id, "toggle_payment", workspace_id=workspace_id,
                 details={"old": old_value, "new": payload.payment_enabled})
    await db.commit()
    return {"workspace_id": str(workspace_id), "payment_enabled": ws.payment_enabled}


# ── Profitability dashboard ────────────────────────────────────────────────────

@router.get("/profitability", response_model=ProfitabilityView)
async def profitability(
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Platform-level revenue vs compute cost vs margin.
    Revenue = sum of price_usd_monthly for active subscriptions.
    Cost = sum of all usage_events.cost_usd in the current period.
    """
    # Revenue from active subscriptions
    rev_result = await db.execute(
        select(func.coalesce(func.sum(Subscription.price_usd_monthly), 0)).where(
            Subscription.status == "active"
        )
    )
    total_revenue = Decimal(str(rev_result.scalar()))

    # Total compute cost (all time — simplified; in prod would be period-scoped)
    cost_result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0))
    )
    total_cost = Decimal(str(cost_result.scalar()))

    margin = total_revenue - total_cost
    margin_pct = float(margin / total_revenue) if total_revenue > 0 else None

    # Workspace and subscription counts
    ws_count_result = await db.execute(select(func.count()).select_from(Workspace))
    ws_count = ws_count_result.scalar() or 0

    active_sub_result = await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.status == "active")
    )
    active_subs = active_sub_result.scalar() or 0

    # At-risk: workspaces whose period spend > 80% of included_budget
    subs_result = await db.execute(
        select(Subscription).where(Subscription.status == "active")
    )
    all_subs = subs_result.scalars().all()
    at_risk = []
    for sub in all_subs:
        if not sub.current_period_start:
            continue
        ps_result = await db.execute(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                UsageEvent.workspace_id == sub.workspace_id,
                UsageEvent.is_overage == False,
                UsageEvent.created_at >= sub.current_period_start,
            )
        )
        spend = Decimal(str(ps_result.scalar()))
        threshold = sub.included_budget_usd * Decimal("0.8")
        if spend >= threshold:
            at_risk.append({
                "workspace_id": str(sub.workspace_id),
                "spend_usd": str(spend),
                "included_budget_usd": str(sub.included_budget_usd),
                "pct": round(float(spend / sub.included_budget_usd), 2) if sub.included_budget_usd > 0 else None,
            })

    return ProfitabilityView(
        total_revenue_usd=total_revenue,
        total_compute_cost_usd=total_cost,
        realized_margin_usd=margin,
        margin_pct=margin_pct,
        workspace_count=ws_count,
        active_subscriptions=active_subs,
        at_risk_workspaces=at_risk,
    )


# ── Audit log ──────────────────────────────────────────────────────────────────

@router.get("/audit-log", response_model=list[AuditEntry])
async def get_audit_log(
    limit: int = 100,
    admin: User = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(min(limit, 500))
    )
    return result.scalars().all()
