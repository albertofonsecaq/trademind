"""
Broker / paper-trading API — per-user, never shared at workspace level.

Status flow enforced here:
  proposed  → (user clicks Confirm)  → confirmed   [confirmed_at set]
  confirmed → (user clicks Submit)   → submitted   [alpaca_order_id set]
  submitted → (user clicks Refresh)  → filled | rejected | cancelled
  proposed | confirmed → (user clicks Cancel) → cancelled

No order ever skips the confirmed step before submission — this is a hard constraint.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.broker_order import BrokerOrder
from app.models.journal_entry import JournalEntry
from app.models.journal_outcome import JournalOutcome
from app.connectors.alpaca import AlpacaConnector, AlpacaError, ALPACA_TERMINAL_MAP

router = APIRouter(prefix="/users/me", tags=["broker"])

_VALID_ACTIONS = {"long", "short"}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class OrderCreate(BaseModel):
    strategy_card_id: uuid.UUID | None = None
    symbol: str
    action: str                 # long | short
    entry: Decimal | None = None
    target: Decimal | None = None
    stop: Decimal | None = None
    size: Decimal               # shares
    notes: str | None = None


class BrokerOrderOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    strategy_card_id: uuid.UUID | None
    symbol: str
    action: str
    entry: Decimal | None
    target: Decimal | None
    stop: Decimal | None
    size: Decimal
    status: str
    mode: str
    notes: str | None
    confirmed_at: datetime | None
    alpaca_order_id: str | None
    filled_price: Decimal | None
    created_at: datetime
    updated_at: datetime


class JournalEntryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_id: uuid.UUID
    strategy_card_id: uuid.UUID | None
    broker_order_id: uuid.UUID | None
    symbol: str
    action: str
    entry: Decimal
    exit: Decimal | None
    size: Decimal
    mode: str
    notes: str | None
    timestamp: datetime
    created_at: datetime
    # Outcome data joined in at query time (not an ORM relationship)
    outcome: str | None = None
    target_price: Decimal | None = None
    stop_price: Decimal | None = None
    max_adverse_excursion: Decimal | None = None
    max_favorable_excursion: Decimal | None = None


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_order(db: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID) -> BrokerOrder:
    result = await db.execute(
        select(BrokerOrder).where(BrokerOrder.id == order_id, BrokerOrder.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# ── Orders ────────────────────────────────────────────────────────────────────

@router.post("/orders", response_model=BrokerOrderOut, status_code=201)
async def propose_order(
    payload: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new proposed order. No trade is placed yet."""
    if payload.action not in _VALID_ACTIONS:
        raise HTTPException(status_code=422, detail=f"action must be one of {_VALID_ACTIONS}")
    if payload.size <= 0:
        raise HTTPException(status_code=422, detail="size must be positive")

    order = BrokerOrder(
        user_id=current_user.id,
        strategy_card_id=payload.strategy_card_id,
        symbol=payload.symbol.upper().strip(),
        action=payload.action,
        entry=payload.entry,
        target=payload.target,
        stop=payload.stop,
        size=payload.size,
        notes=payload.notes,
        status="proposed",
        mode="paper",
        updated_at=datetime.now(timezone.utc),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


@router.get("/orders", response_model=list[BrokerOrderOut])
async def list_orders(
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(BrokerOrder).where(BrokerOrder.user_id == current_user.id)
    if status:
        q = q.where(BrokerOrder.status == status)
    q = q.order_by(BrokerOrder.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/orders/{order_id}", response_model=BrokerOrderOut)
async def get_order(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_order(db, order_id, current_user.id)


@router.post("/orders/{order_id}/confirm", response_model=BrokerOrderOut)
async def confirm_order(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Explicit human confirmation step. Moves proposed → confirmed.
    This is a hard gate — no order can be submitted without passing through here.
    """
    order = await _get_order(db, order_id, current_user.id)
    if order.status != "proposed":
        raise HTTPException(status_code=409, detail=f"Order is already in '{order.status}' status")

    order.status = "confirmed"
    order.confirmed_at = datetime.now(timezone.utc)
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/orders/{order_id}/submit", response_model=BrokerOrderOut)
async def submit_order(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a confirmed order to Alpaca paper trading.
    Requires ALPACA_API_KEY + ALPACA_SECRET_KEY in server config.
    """
    order = await _get_order(db, order_id, current_user.id)
    if order.status != "confirmed":
        raise HTTPException(
            status_code=409,
            detail=f"Order must be confirmed before submission (current status: '{order.status}')",
        )

    connector = AlpacaConnector()
    try:
        result = await connector.submit_order(
            symbol=order.symbol,
            action=order.action,
            size=order.size,
            entry=order.entry,
        )
    except AlpacaError as e:
        raise HTTPException(status_code=503, detail=str(e))

    order.alpaca_order_id = result.get("id")
    order.status = "submitted"
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/orders/{order_id}/cancel", response_model=BrokerOrderOut)
async def cancel_order(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a proposed, confirmed, or submitted order."""
    order = await _get_order(db, order_id, current_user.id)
    if order.status in ("filled", "rejected", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Order is already in terminal status '{order.status}'")

    if order.status == "submitted" and order.alpaca_order_id:
        connector = AlpacaConnector()
        try:
            await connector.cancel_order(order.alpaca_order_id)
        except AlpacaError as e:
            raise HTTPException(status_code=503, detail=str(e))

    order.status = "cancelled"
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


@router.post("/orders/{order_id}/refresh", response_model=BrokerOrderOut)
async def refresh_order_status(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll Alpaca for the latest status of a submitted order.
    If the order has filled, a JournalEntry is created automatically.
    """
    order = await _get_order(db, order_id, current_user.id)
    if order.status != "submitted":
        raise HTTPException(status_code=409, detail="Only submitted orders can be refreshed")
    if not order.alpaca_order_id:
        raise HTTPException(status_code=409, detail="No Alpaca order ID on record")

    connector = AlpacaConnector()
    try:
        alpaca_data = await connector.get_order(order.alpaca_order_id)
    except AlpacaError as e:
        raise HTTPException(status_code=503, detail=str(e))

    new_status = ALPACA_TERMINAL_MAP.get(alpaca_data.get("status", ""), "submitted")
    order.status = new_status
    order.updated_at = datetime.now(timezone.utc)

    if new_status == "filled":
        filled_price_str = alpaca_data.get("filled_avg_price")
        if filled_price_str:
            order.filled_price = Decimal(str(filled_price_str))

        # Create journal entry if one doesn't already exist for this order
        existing_journal = await db.execute(
            select(JournalEntry).where(JournalEntry.broker_order_id == order.id)
        )
        if not existing_journal.scalar_one_or_none():
            entry_price = order.filled_price or order.entry or Decimal("0")
            journal = JournalEntry(
                user_id=current_user.id,
                strategy_card_id=order.strategy_card_id,
                broker_order_id=order.id,
                symbol=order.symbol,
                action=order.action,
                entry=entry_price,
                exit=None,
                size=order.size,
                mode=order.mode,
                notes=order.notes,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(journal)

    await db.commit()
    await db.refresh(order)
    return order


# ── Journal ───────────────────────────────────────────────────────────────────

@router.get("/journal", response_model=list[JournalEntryOut])
async def list_journal(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List journal entries with outcome data joined in."""
    rows = await db.execute(
        select(JournalEntry, JournalOutcome)
        .outerjoin(JournalOutcome, JournalOutcome.journal_entry_id == JournalEntry.id)
        .where(JournalEntry.user_id == current_user.id)
        .order_by(JournalEntry.timestamp.desc())
    )
    results = []
    for entry, outcome in rows.all():
        out = JournalEntryOut.model_validate(entry)
        if outcome:
            out.outcome = outcome.outcome
            out.target_price = outcome.target_price
            out.stop_price = outcome.stop_price
            out.max_adverse_excursion = outcome.max_adverse_excursion
            out.max_favorable_excursion = outcome.max_favorable_excursion
        results.append(out)
    return results


class JournalUpdate(BaseModel):
    exit: Decimal | None = None
    notes: str | None = None


@router.patch("/journal/{entry_id}", response_model=JournalEntryOut)
async def update_journal_entry(
    entry_id: uuid.UUID,
    payload: JournalUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record an exit price (close the trade) or update notes."""
    result = await db.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.user_id == current_user.id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    if payload.exit is not None:
        entry.exit = payload.exit
    if payload.notes is not None:
        entry.notes = payload.notes
    await db.commit()
    await db.refresh(entry)

    out = JournalEntryOut.model_validate(entry)
    outcome_result = await db.execute(
        select(JournalOutcome).where(JournalOutcome.journal_entry_id == entry.id)
    )
    outcome = outcome_result.scalar_one_or_none()
    if outcome:
        out.outcome = outcome.outcome
        out.target_price = outcome.target_price
        out.stop_price = outcome.stop_price
        out.max_adverse_excursion = outcome.max_adverse_excursion
        out.max_favorable_excursion = outcome.max_favorable_excursion
    return out


class ScoreResult(BaseModel):
    entries_scored: int
    message: str


@router.post("/journal/score", response_model=ScoreResult, status_code=202)
async def score_journal(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger scoring of all journal entries for the current user."""
    background_tasks.add_task(_run_journal_score_bg, user_id=current_user.id)
    return ScoreResult(
        entries_scored=0,
        message="Journal scoring started in background.",
    )


async def _run_journal_score_bg(user_id: uuid.UUID) -> None:
    from app.services.journal_validation_service import run_journal_validation
    async with AsyncSessionLocal() as db:
        try:
            await run_journal_validation(db, user_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Journal scoring failed for user %s: %s", user_id, e)
