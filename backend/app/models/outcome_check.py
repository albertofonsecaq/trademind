import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Integer, Numeric, Date, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class OutcomeCheck(Base):
    """
    Result of validating a single trade_idea against real market price data.
    One row per trade_idea — upserted on each validation run.
    """
    __tablename__ = "outcome_checks"
    __table_args__ = (UniqueConstraint("trade_idea_id", name="uq_outcome_trade_idea"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_idea_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trade_ideas.id"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)  # long | short
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # won | lost | open | expired | inconclusive
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="inconclusive")
    outcome_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # How far price moved against the trade before resolution (risk measure)
    max_adverse_excursion: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    # How far price moved in favor before resolution
    max_favorable_excursion: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)

    data_source: Mapped[str] = mapped_column(String(50), nullable=False, default="yfinance")
    checked_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
