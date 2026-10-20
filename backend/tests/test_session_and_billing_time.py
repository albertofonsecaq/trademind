"""
The two timestamps in #36 that a user feels — for #8.

Most of this batch is bookkeeping columns. Two are not:

- a token's expiry decides when somebody is logged out, and a wrong instant
  there is the one failure in this work a user notices immediately;
- a billing period decides what counts as this month's spend, which drives the
  budget cap, whether ingestion pauses, and what Stripe meters.

Stripe hands us aware timestamps (`_ts` builds them with `tz=timezone.utc`),
so until this batch those writes were aware values going into naive-bound
columns — the #8 DataError. There are no subscription rows in the database,
which is consistent with that path never having completed.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy import select

from app.core.clock import now_utc
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token


class TestATokensLife:
    def _expiry_of(self, token: str) -> datetime:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

    def test_an_access_token_expires_when_the_setting_says(self):
        """
        Not "a token is produced" — the instant itself, because this is where a
        wrong one logs somebody out early or keeps them in too long.
        """
        expected = now_utc() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

        expiry = self._expiry_of(create_access_token("a-user"))

        assert abs(expiry - expected) < timedelta(seconds=5)

    def test_a_refresh_token_expires_when_the_setting_says(self):
        expected = now_utc() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        expiry = self._expiry_of(create_refresh_token("a-user"))

        assert abs(expiry - expected) < timedelta(seconds=5)

    def test_an_explicit_lifetime_is_honoured(self):
        expected = now_utc() + timedelta(minutes=7)

        expiry = self._expiry_of(create_access_token("a-user", timedelta(minutes=7)))

        assert abs(expiry - expected) < timedelta(seconds=5)

    def test_the_expiry_does_not_move_with_the_machines_zone(self, monkeypatch):
        """
        The change this batch makes, pinned: an aware now and a naive one
        encode the same epoch, so nobody's session length depends on where the
        process runs.
        """
        import time

        lifetimes = []
        for zone in ("Pacific/Kiritimati", "Pacific/Midway"):    # UTC+14 and UTC-11
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            lifetimes.append(self._expiry_of(create_access_token("a-user")) - now_utc())

        assert abs(lifetimes[0] - lifetimes[1]) < timedelta(seconds=5)


class TestABillingPeriod:
    """
    Database-backed: the period boundaries come from Stripe as aware values and
    are compared against usage events in SQL. Both sides of that comparison are
    columns this batch moved.
    """

    pytestmark = pytest.mark.db

    async def _a_workspace(self, session):
        import uuid

        from app.models.user import User
        from app.models.workspace import Workspace

        user = User(
            id=uuid.uuid4(), email=f"billing-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
        )
        session.add(user)
        await session.flush()
        workspace = Workspace(
            id=uuid.uuid4(), name="billing check", owner_user_id=user.id,
            topic_scope="forex",
        )
        session.add(workspace)
        await session.flush()
        return workspace

    async def _a_subscription(self, session):
        """A workspace with an active subscription, and the period's start."""
        import uuid

        from app.models.subscription import Subscription

        workspace = await self._a_workspace(session)
        period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sub = Subscription(
            id=uuid.uuid4(), workspace_id=workspace.id, plan_name="solo",
            price_usd_monthly=29, included_budget_usd=30, status="active",
            current_period_start=period_start,
            current_period_end=period_start + timedelta(days=31),
        )
        session.add(sub)
        await session.flush()
        return workspace, sub, period_start

    async def _usage(self, session, workspace, created_at, cost, overage=False):
        import uuid

        from app.models.usage_event import UsageEvent

        session.add(
            UsageEvent(
                id=uuid.uuid4(), workspace_id=workspace.id,
                task_type="relevance_filter", provider_model="haiku",
                cost_usd=Decimal(cost), is_overage=overage, created_at=created_at,
            )
        )

    async def test_a_stripe_period_can_be_stored_at_all(self, db_session):
        """
        It could not before this batch: `_ts` builds aware values and the column
        was bound naive, which is #8's DataError. This is the write that was
        failing.
        """
        import uuid

        from app.models.subscription import Subscription
        from app.services.billing_service import _ts

        workspace = await self._a_workspace(db_session)
        period_start = _ts(1_767_225_600)          # 2026-01-01T00:00:00Z
        period_end = _ts(1_769_904_000)            # 2026-02-01T00:00:00Z

        sub = Subscription(
            id=uuid.uuid4(), workspace_id=workspace.id, plan_name="solo",
            price_usd_monthly=29, included_budget_usd=30,
            status="active", current_period_start=period_start,
            current_period_end=period_end,
        )
        db_session.add(sub)
        await db_session.flush()

        stored = (
            await db_session.execute(
                select(Subscription.current_period_start, Subscription.current_period_end)
                .where(Subscription.id == sub.id)
            )
        ).one()

        assert stored == (period_start, period_end)
        assert all(value.tzinfo is not None for value in stored)

    async def test_period_spend_counts_from_the_boundary(self, db_session):
        """
        The real query, not a reimplementation of it.

        An earlier version of this test wrote its own count() with the same
        where-clause, which proved the comparison worked and nothing about the
        function the budget cap actually calls — it omitted the overage filter
        and summed nothing. get_period_spend is what is_budget_exhausted uses,
        so that is what gets exercised: an event on the period boundary is
        inside it, one a second earlier is not, and an overage event is excluded
        whichever side it falls on.
        """
        from app.services.billing_service import get_period_spend

        workspace, sub, period_start = await self._a_subscription(db_session)

        await self._usage(db_session, workspace, period_start - timedelta(seconds=1), "0.10")
        await self._usage(db_session, workspace, period_start, "0.01")
        await self._usage(db_session, workspace, period_start + timedelta(seconds=1), "0.02")
        await self._usage(
            db_session, workspace, period_start + timedelta(hours=1), "5.00", overage=True
        )
        await db_session.flush()

        spend = await get_period_spend(db_session, workspace.id, sub)

        # 0.01 on the boundary + 0.02 after it. Not the 0.10 before, not the
        # 5.00 of overage, which is metered separately rather than capped.
        assert spend == Decimal("0.03")

    async def test_period_spend_is_the_same_whatever_zone_the_server_runs_in(
        self, db_session
    ):
        """
        What the batch is for: the boundary is an instant, so moving the
        machine's clock must not move what falls inside the billing period.
        """
        import time

        from app.services.billing_service import get_period_spend

        workspace, sub, period_start = await self._a_subscription(db_session)
        await self._usage(db_session, workspace, period_start + timedelta(seconds=1), "0.02")
        await self._usage(db_session, workspace, period_start - timedelta(seconds=1), "0.10")
        await db_session.flush()

        spends = []
        for zone in ("Pacific/Kiritimati", "Pacific/Midway"):
            time.tzset()
            spends.append(await get_period_spend(db_session, workspace.id, sub))

        assert spends[0] == spends[1] == Decimal("0.02")

    async def test_every_tenancy_column_round_trips(self, db_session):
        """The rest of the batch: written, read back, and the defaults are aware."""
        import uuid

        from app.models.admin_audit_log import AdminAuditLog
        from app.models.user import User
        from app.models.workspace import Workspace
        from app.models.workspace_member import WorkspaceMember

        workspace = await self._a_workspace(db_session)
        member = WorkspaceMember(
            id=uuid.uuid4(), workspace_id=workspace.id,
            user_id=workspace.owner_user_id, role="owner",
        )
        db_session.add(member)
        audit = AdminAuditLog(
            id=uuid.uuid4(), admin_user_id=workspace.owner_user_id,
            action="created_workspace",
        )
        db_session.add(audit)
        await db_session.flush()

        rows = (
            await db_session.execute(
                select(User.created_at).where(User.id == workspace.owner_user_id)
            )
        ).scalar_one(), (
            await db_session.execute(
                select(Workspace.created_at).where(Workspace.id == workspace.id)
            )
        ).scalar_one(), (
            await db_session.execute(
                select(WorkspaceMember.joined_at).where(WorkspaceMember.id == member.id)
            )
        ).scalar_one(), (
            await db_session.execute(
                select(AdminAuditLog.created_at).where(AdminAuditLog.id == audit.id)
            )
        ).scalar_one()

        for value in rows:
            assert value.tzinfo is not None
            assert abs(value - now_utc()) < timedelta(minutes=5)
