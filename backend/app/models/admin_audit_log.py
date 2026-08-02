import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AdminAuditLog(Base):
    """Every super-admin action is recorded here — who did what, to which workspace/user, and when."""
    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # "create_workspace" | "add_member" | "remove_member" | "toggle_payment" | "override_subscription"
    target_workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, server_default=func.now(), index=True
    )
