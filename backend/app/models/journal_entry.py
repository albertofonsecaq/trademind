import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class JournalEntry(Base):
    """
    Record of every executed trade, paper or live, per-user.
    Created automatically when a submitted BrokerOrder is filled, or manually.
    Future: feeds back into validation layer to score user execution vs. strategy card predictions.
    """
    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    strategy_card_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_cards.id"), nullable=True)
    broker_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("broker_orders.id"), nullable=True)

    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)       # long | short
    entry: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    exit: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    size: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )

    broker_order: Mapped["BrokerOrder | None"] = relationship("BrokerOrder", back_populates="journal_entries")
