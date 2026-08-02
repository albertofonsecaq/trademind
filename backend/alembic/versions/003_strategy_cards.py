"""strategy cards and plan items

Revision ID: 003
Revises: 002
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_cards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("setup_type", sa.String(100), nullable=False),
        sa.Column("symbol_scope", sa.String(20), nullable=False),
        sa.Column("description_en", sa.Text, nullable=False),
        sa.Column("description_es", sa.Text, nullable=False),
        sa.Column("flowchart_spec", JSONB, nullable=False, server_default="{}"),
        sa.Column("supporting_evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence_interval", JSONB, nullable=True),
        sa.Column("preliminary_confidence", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version_history", JSONB, nullable=False, server_default="[]"),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "symbol_scope", "setup_type", name="uq_strategy_card"),
    )
    op.create_index("ix_strategy_cards_workspace_id", "strategy_cards", ["workspace_id"])
    op.create_index("ix_strategy_cards_symbol_scope", "strategy_cards", ["symbol_scope"])

    op.create_table(
        "plan_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_card_id", UUID(as_uuid=True), sa.ForeignKey("strategy_cards.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_notes", sa.Text, nullable=True),
        sa.Column("risk_tolerance_match", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="watching"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "strategy_card_id", name="uq_plan_item_user_card"),
    )
    op.create_index("ix_plan_items_user_id", "plan_items", ["user_id"])


def downgrade() -> None:
    op.drop_table("plan_items")
    op.drop_table("strategy_cards")
