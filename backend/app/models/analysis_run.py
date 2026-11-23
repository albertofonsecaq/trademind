import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now_utc
from app.core.database import Base

# What was run. Pattern mining writes strategy cards; validation checks them
# against market prices.
MINING = "mining"
VALIDATION = "validation"
RunKind = Literal["mining", "validation"]

# How it ended, in a backfill outcome's words (CONTEXT.md): completed lost
# nothing, partial finished with some clusters or cards failing, failed hit a
# failure that would repeat for everything left — a rejected API key — and
# stopped, or never finished because the server went away.
RUNNING = "running"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"
RunStatus = Literal["running", "completed", "partial", "failed"]


class AnalysisRun(Base):
    """
    One pattern-mining or validation run over a workspace, and how it ended (#86).

    Both run in the background, so the request that starts one cannot say how
    it went. This row can: it is what the Strategy Library reads to show a run
    in progress and then its outcome, to every member of the workspace.
    """
    __tablename__ = "analysis_runs"
    __table_args__ = (
        # At most one running run per workspace, of either kind: both write the
        # workspace's strategy cards, and mining holds its writes for minutes
        # before committing, so the two would overwrite each other. Held by the
        # database rather than by a check-then-insert two requests could race.
        Index(
            "uq_analysis_runs_one_running",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        # The query the Library makes: the newest run of each kind.
        Index("ix_analysis_runs_workspace_kind_started", "workspace_id", "kind", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RUNNING)
    # Null for a run the weekly schedule started.
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    cards_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cards_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # One {"subject": ..., "reason": ...} per cluster or card that failed while
    # the run carried on: "BTCUSD/wedge" and why.
    failures: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # Why the whole run stopped, when it did.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@dataclass
class RunOutcome:
    """What a run did, and what went wrong, in the terms its row records."""
    cards_created: int = 0
    cards_updated: int = 0
    # One {"subject": ..., "reason": ...} per cluster or card that failed while
    # the run carried on.
    failures: list[dict] = field(default_factory=list)
    # Why the whole run stopped, when a failure would have repeated for
    # everything left.
    stopped_because: str | None = None

    @property
    def status(self) -> str:
        if self.stopped_because:
            return FAILED
        return PARTIAL if self.failures else COMPLETED
