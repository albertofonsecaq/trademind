import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Integer, Numeric, Date, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class JournalOutcome(Base):
    """
    Outcome check for a single JournalEntry — mirrors OutcomeCheck for trade_ideas,
    but scoped to the user's personal executions rather than channel predictions.
    One row per journal_entry (unique constraint).
    Scored when:
      - journal_entry.exit is set: compare exit vs target/stop from linked broker_order
      - no exit but linked broker_order has target/stop: scan market data from trade date
      - neither: outcome = inconclusive
    """
    __tablename__ = "journal_outcomes"
    __table_args__ = (UniqueConstraint("journal_entry_id", name="uq_journal_outcome_entry"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    strategy_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_cards.id"), nullable=True, index=True
    )

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    actual_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="inconclusive")
    outcome_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    max_adverse_excursion: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    max_favorable_excursion: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    data_source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    checked_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
