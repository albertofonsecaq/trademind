import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class BrokerOrder(Base):
    """
    Per-user paper (or future live) order. Status flow:
      proposed → confirmed → submitted → filled | rejected | cancelled
    A confirmed_at timestamp is required before any order can be submitted —
    that transition is the mandatory explicit human approval step.
    """
    __tablename__ = "broker_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    strategy_card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_cards.id"), nullable=True)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)          # long | short
    entry: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)   # limit price
    target: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)  # take-profit
    stop: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)    # stop-loss
    size: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)    # qty in shares
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed", index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filled_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    journal_entries: Mapped[list["JournalEntry"]] = relationship("JournalEntry", back_populates="broker_order")
