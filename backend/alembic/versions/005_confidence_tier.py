"""confidence tier on strategy cards

Revision ID: 005
Revises: 004
Create Date: 2026-08-01

"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategy_cards",
        sa.Column(
            "confidence_tier",
            sa.String(20),
            nullable=False,
            server_default="still_learning",
        ),
    )
    op.create_index("ix_strategy_cards_confidence_tier", "strategy_cards", ["confidence_tier"])


def downgrade() -> None:
    op.drop_index("ix_strategy_cards_confidence_tier", "strategy_cards")
    op.drop_column("strategy_cards", "confidence_tier")
