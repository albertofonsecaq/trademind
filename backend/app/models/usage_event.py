import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Numeric, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_units: Mapped[int] = mapped_column(nullable=False, default=0)
    output_units: Mapped[int] = mapped_column(nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False, default=Decimal("0"))
    is_overage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), server_default=func.now(), index=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="usage_events")
