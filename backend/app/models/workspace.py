import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Boolean, Numeric, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    monthly_budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    payment_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    topic_scope: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, server_default=func.now())

    members: Mapped[list["WorkspaceMember"]] = relationship("WorkspaceMember", back_populates="workspace")
    subscription: Mapped["Subscription | None"] = relationship("Subscription", back_populates="workspace", uselist=False)
    usage_events: Mapped[list["UsageEvent"]] = relationship("UsageEvent", back_populates="workspace")
