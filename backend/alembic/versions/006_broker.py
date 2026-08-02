"""broker_orders and journal_entries tables

Revision ID: 006
Revises: 005
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_card_id", UUID(as_uuid=True), sa.ForeignKey("strategy_cards.id"), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("entry", sa.Numeric(12, 4), nullable=True),
        sa.Column("target", sa.Numeric(12, 4), nullable=True),
        sa.Column("stop", sa.Numeric(12, 4), nullable=True),
        sa.Column("size", sa.Numeric(14, 4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("mode", sa.String(10), nullable=False, server_default="paper"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alpaca_order_id", sa.String(64), nullable=True),
        sa.Column("filled_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_broker_orders_user_id", "broker_orders", ["user_id"])
    op.create_index("ix_broker_orders_status", "broker_orders", ["status"])
    op.create_index("ix_broker_orders_symbol", "broker_orders", ["symbol"])

    op.create_table(
        "journal_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_card_id", UUID(as_uuid=True), sa.ForeignKey("strategy_cards.id"), nullable=True),
        sa.Column("broker_order_id", UUID(as_uuid=True), sa.ForeignKey("broker_orders.id"), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("entry", sa.Numeric(12, 4), nullable=False),
        sa.Column("exit", sa.Numeric(12, 4), nullable=True),
        sa.Column("size", sa.Numeric(14, 4), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False, server_default="paper"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_journal_entries_user_id", "journal_entries", ["user_id"])
    op.create_index("ix_journal_entries_symbol", "journal_entries", ["symbol"])


def downgrade() -> None:
    op.drop_table("journal_entries")
    op.drop_table("broker_orders")
