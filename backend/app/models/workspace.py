import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import DateTime, String, Boolean, Numeric, Text, ForeignKey, func
from sqlalchemy.sql import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.clock import now_utc
from app.core.database import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    monthly_budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    payment_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # One switch for every source in the workspace. Deliberately orthogonal to
    # each source's own paused state, so switching the workspace back on brings
    # back exactly the sources that were running before (#13).
    ingestion_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_false()
    )
    topic_scope: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())

    members: Mapped[list["WorkspaceMember"]] = relationship("WorkspaceMember", back_populates="workspace")
    subscription: Mapped["Subscription | None"] = relationship("Subscription", back_populates="workspace", uselist=False)
    usage_events: Mapped[list["UsageEvent"]] = relationship("UsageEvent", back_populates="workspace")
