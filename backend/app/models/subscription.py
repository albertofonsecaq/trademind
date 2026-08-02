import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Numeric, Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, unique=True)
    plan_name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_usd_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    included_budget_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    included_seats: Mapped[int] = mapped_column(nullable=False, default=1)
    price_per_seat_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    included_budget_per_seat_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    overage_rate_multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("1.5"))
    overage_ceiling_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("50"))
    current_period_overage_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    payment_provider_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_provider_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="inactive")
    manually_overridden_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(nullable=True)
    stripe_subscription_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="subscription")
