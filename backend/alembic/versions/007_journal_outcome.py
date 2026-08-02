"""journal_outcomes table

Revision ID: 007
Revises: 006
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_outcomes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("journal_entry_id", UUID(as_uuid=True), sa.ForeignKey("journal_entries.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_card_id", UUID(as_uuid=True), sa.ForeignKey("strategy_cards.id"), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("target_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("stop_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("actual_exit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("trade_date", sa.Date, nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="inconclusive"),
        sa.Column("outcome_date", sa.Date, nullable=True),
        sa.Column("holding_days", sa.Integer, nullable=True),
        sa.Column("max_adverse_excursion", sa.Numeric(12, 6), nullable=True),
        sa.Column("max_favorable_excursion", sa.Numeric(12, 6), nullable=True),
        sa.Column("data_source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False, server_default="30"),
        sa.UniqueConstraint("journal_entry_id", name="uq_journal_outcome_entry"),
    )
    op.create_index("ix_journal_outcomes_user_id", "journal_outcomes", ["user_id"])
    op.create_index("ix_journal_outcomes_strategy_card_id", "journal_outcomes", ["strategy_card_id"])


def downgrade() -> None:
    op.drop_table("journal_outcomes")
