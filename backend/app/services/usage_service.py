"""
Every LLM, transcription, vision, and embedding call must log a UsageEvent
through this service. Wire into every provider call from the very first use.

A UsageEvent records that a call *happened*, which stays true whatever becomes
of the work it was for — so it is written in its own transaction rather than the
caller's. It used to share the caller's, and because a message is processed
inside one savepoint (#2), a distillation failure rolled back the relevance
call's event along with the message: tokens spent, provider billing for them,
nothing recorded (#27). The budget cap, the overage metering and the margin
protection are all derived from these rows, so an unlogged call is spend that
never reaches the cap.

Nothing here references the evidence a call was about — source_id is a plain
column, not a foreign key — so a row committed on its own cannot be orphaned by
work that rolls back.
"""
import logging
import uuid
from decimal import Decimal

from app.core.database import AsyncSessionLocal
from app.models.usage_event import UsageEvent

log = logging.getLogger(__name__)


async def log_usage_event(
    *,
    workspace_id: uuid.UUID,
    task_type: str,
    provider_model: str,
    input_units: int = 0,
    output_units: int = 0,
    cost_usd: Decimal = Decimal("0"),
    is_overage: bool = False,
    source_id: uuid.UUID | None = None,
) -> None:
    """
    Record one provider call, in a transaction of its own.

    Deliberately takes no session: a caller cannot accidentally enlist this in a
    transaction that might undo it, which was the bug. That makes this the one
    service that opens its own session — every other one is handed the caller's
    — and the exception is the whole point rather than an oversight.

    A failure here is logged and swallowed. Letting it propagate would abort
    work that had already succeeded and, in a run of many messages, would fail
    every remaining one in turn: the #9 shape, over bookkeeping. And it would
    not save the record either — if this write cannot happen, the call goes
    unrecorded whether or not the caller also dies. The loud log is the trail.
    """
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
    try:
        async with AsyncSessionLocal() as db:
            db.add(event)
            await db.commit()
    except Exception as e:
        log.error(
            "Could not record a %s usage event for workspace %s (%s): %s",
            task_type, workspace_id, provider_model, e, exc_info=True,
        )
