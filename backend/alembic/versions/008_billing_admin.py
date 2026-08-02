"""billing admin: add current_period_start to subscriptions, create admin_audit_logs

Revision ID: 008
Revises: 007
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add current_period_start to subscriptions (needed to correctly bound
    # per-period spend aggregation in the overage check)
    op.add_column(
        "subscriptions",
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
    )
    # Stripe subscription item ID for the base-plan line item (needed for quantity updates)
    op.add_column(
        "subscriptions",
        sa.Column("stripe_subscription_item_id", sa.String(255), nullable=True),
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("admin_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_workspace_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_logs_admin_user_id", "admin_audit_logs", ["admin_user_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_target_workspace_id", "admin_audit_logs", ["target_workspace_id"])
    op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_column("subscriptions", "stripe_subscription_item_id")
    op.drop_column("subscriptions", "current_period_start")
