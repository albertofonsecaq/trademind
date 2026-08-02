"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (needed for Phase 2+)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("auth_provider_id", sa.String(255), nullable=True),
        sa.Column("default_workspace_id", UUID(as_uuid=True), nullable=True),
        sa.Column("preferred_language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("platform_role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("monthly_budget_cap", sa.Numeric(12, 4), nullable=True),
        sa.Column("payment_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("topic_scope", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "workspace_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False, unique=True),
        sa.Column("plan_name", sa.String(100), nullable=False),
        sa.Column("price_usd_monthly", sa.Numeric(12, 4), nullable=False),
        sa.Column("included_budget_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("included_seats", sa.Integer, nullable=False, server_default="1"),
        sa.Column("price_per_seat_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("included_budget_per_seat_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("overage_rate_multiplier", sa.Numeric(6, 4), nullable=False, server_default="1.5"),
        sa.Column("overage_ceiling_usd", sa.Numeric(12, 4), nullable=False, server_default="50"),
        sa.Column("current_period_overage_usd", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("payment_provider_customer_id", sa.String(255), nullable=True),
        sa.Column("payment_provider_subscription_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="inactive"),
        sa.Column("manually_overridden_by", UUID(as_uuid=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("provider_model", sa.String(100), nullable=False),
        sa.Column("input_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_units", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=False, server_default="0"),
        sa.Column("is_overage", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_events_workspace_id", "usage_events", ["workspace_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("subscriptions")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")
