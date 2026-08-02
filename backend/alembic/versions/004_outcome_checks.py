"""outcome checks and walk-forward results

Revision ID: 004
Revises: 003
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outcome_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_idea_id", UUID(as_uuid=True), sa.ForeignKey("trade_ideas.id"), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("target_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("trade_date", sa.Date, nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="inconclusive"),
        sa.Column("outcome_date", sa.Date, nullable=True),
        sa.Column("holding_days", sa.Integer, nullable=True),
        sa.Column("max_adverse_excursion", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_favorable_excursion", sa.Numeric(20, 8), nullable=True),
        sa.Column("data_source", sa.String(50), nullable=False, server_default="yfinance"),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("window_days", sa.Integer, nullable=False, server_default="30"),
        sa.UniqueConstraint("trade_idea_id", name="uq_outcome_trade_idea"),
    )
    op.create_index("ix_outcome_checks_workspace_id", "outcome_checks", ["workspace_id"])
    op.create_index("ix_outcome_checks_symbol", "outcome_checks", ["symbol"])

    # Add walk-forward validation result to strategy_cards
    op.add_column(
        "strategy_cards",
        sa.Column("walk_forward_result", JSONB, nullable=True),
    )
    # Add validation_updated_at to track when stats were last refreshed
    op.add_column(
        "strategy_cards",
        sa.Column("validation_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategy_cards", "validation_updated_at")
    op.drop_column("strategy_cards", "walk_forward_result")
    op.drop_table("outcome_checks")
