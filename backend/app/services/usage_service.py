"""
Every LLM, transcription, vision, and embedding call must log a UsageEvent
through this service. Wire into every provider call from the very first use.
"""
import uuid
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.usage_event import UsageEvent


async def log_usage_event(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task_type: str,
    provider_model: str,
    input_units: int = 0,
    output_units: int = 0,
    cost_usd: Decimal = Decimal("0"),
    is_overage: bool = False,
    source_id: uuid.UUID | None = None,
) -> UsageEvent:
    event = UsageEvent(
        workspace_id=workspace_id,
        task_type=task_type,
        provider_model=provider_model,
        input_units=input_units,
        output_units=output_units,
        cost_usd=cost_usd,
        is_overage=is_overage,
        source_id=source_id,
    )
    db.add(event)
    await db.flush()
    return event
