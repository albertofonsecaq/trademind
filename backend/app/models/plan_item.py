import uuid
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, func, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class PlanItem(Base):
    """Personal curation layer — scoped to user_id, never visible to other workspace members."""
    __tablename__ = "plan_items"
    __table_args__ = (UniqueConstraint("user_id", "strategy_card_id", name="uq_plan_item_user_card"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategy_cards.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_tolerance_match: Mapped[str | None] = mapped_column(String(20), nullable=True)  # low|medium|high
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="watching")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    card: Mapped["StrategyCard"] = relationship("StrategyCard", back_populates="plan_items")
