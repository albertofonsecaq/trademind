import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, ForeignKey, func, Text, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class StrategyCard(Base):
    __tablename__ = "strategy_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol_scope: Mapped[str] = mapped_column(String(20), nullable=False)  # ticker or "general"
    description_en: Mapped[str] = mapped_column(Text, nullable=False)
    description_es: Mapped[str] = mapped_column(Text, nullable=False)
    flowchart_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # List of trade_idea UUIDs as strings
    supporting_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_interval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 0-1 score from sample_size + source diversity, before validation data is available
    preliminary_confidence: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0"))
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # still_learning | developing | established
    confidence_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="still_learning")
    walk_forward_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_updated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_updated: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), server_default=func.now())

    plan_items: Mapped[list["PlanItem"]] = relationship("PlanItem", back_populates="card")
