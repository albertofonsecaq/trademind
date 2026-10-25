import uuid
from datetime import datetime
from sqlalchemy import DateTime, String, ForeignKey, func, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.clock import now_utc
from app.core.database import Base


class AskRecord(Base):
    """
    One question a user asked and the answer synthesized for it.

    Personal layer — scoped to user_id, never visible to other workspace
    members, the same rule a plan item follows. The answer and its cited
    sources are stored as they were returned, so reading a record back never
    needs retrieval or synthesis: the point of keeping it is that a question
    already paid for does not have to be asked again.
    """
    __tablename__ = "ask_records"
    __table_args__ = (
        # The one query this table exists to serve: a user's own records in one
        # workspace, newest first.
        Index("ix_ask_records_user_workspace_created", "user_id", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # The sources the answer cited, exactly as the synthesis returned them. A
    # record with no sources is a real answer ("nothing relevant found"), so the
    # column is an empty list rather than null.
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
