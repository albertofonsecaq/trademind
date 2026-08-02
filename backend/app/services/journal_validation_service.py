"""
Journal validation service — scores user's own trade executions.

Scoring priority:
  1. If journal_entry.exit is set AND broker_order has target/stop:
       Compare exit vs target/stop directly (no market data call needed).
  2. If no exit but broker_order has target/stop:
       Scan market data bars from the trade date (same logic as trade_idea validation).
  3. Otherwise: outcome = inconclusive.

This runs separately from trade_idea validation so the stats are distinct:
  channel_win_rate  = how often the channel's predicted setup worked out
  execution_win_rate = how often the user's actual fills on that setup worked out
"""
from __future__ import annotations
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.connectors.market_data import MarketDataConnector
from app.models.journal_entry import JournalEntry
from app.models.journal_outcome import JournalOutcome
from app.models.broker_order import BrokerOrder
from app.services.validation_service import _check_bars

log = logging.getLogger(__name__)


async def score_journal_entry(
    db: AsyncSession,
    entry: JournalEntry,
    market: MarketDataConnector,
    window_days: int,
) -> JournalOutcome:
    """
    Score one journal entry. Creates or updates the JournalOutcome row.
    Uses _check_bars from validation_service when market data is needed.
    """
    # Skip final states unless re-scoring after exit is recorded
    existing_result = await db.execute(
        select(JournalOutcome).where(JournalOutcome.journal_entry_id == entry.id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing and existing.outcome in ("won", "lost") and not entry.exit:
        return existing  # already terminal and no new exit to apply

    # Get target/stop from linked broker_order
    target: Decimal | None = None
    stop: Decimal | None = None
    if entry.broker_order_id:
        order_result = await db.execute(
            select(BrokerOrder).where(BrokerOrder.id == entry.broker_order_id)
        )
        order = order_result.scalar_one_or_none()
        if order:
            target = order.target
            stop = order.stop

    trade_date = entry.timestamp.date() if entry.timestamp else None

    # Case 1: manual exit recorded
    if entry.exit is not None and target is not None and stop is not None:
        outcome_data = _score_from_exit(
            action=entry.action,
            entry=float(entry.entry),
            exit_price=float(entry.exit),
            target=float(target),
            stop=float(stop),
            trade_date=trade_date,
        )
        data_source = "manual_exit"

    # Case 2: no exit, but have target/stop — scan market data
    elif target is not None and stop is not None and trade_date is not None:
        bars = await market.get_bars(entry.symbol, trade_date, window_days)
        if not bars:
            outcome_data = {"outcome": "open", "outcome_date": None, "holding_days": None,
                            "max_adverse_excursion": None, "max_favorable_excursion": None}
        else:
            outcome_data = _check_bars(
                bars=bars,
                action=entry.action,
                entry=float(entry.entry),
                target=float(target),
                stop=float(stop),
                trade_date=trade_date,
                window_days=window_days,
            )
        data_source = market.data_source_name()

    else:
        outcome_data = {"outcome": "inconclusive", "outcome_date": None, "holding_days": None,
                        "max_adverse_excursion": None, "max_favorable_excursion": None}
        data_source = "manual"

    def _dec(v) -> Decimal | None:
        return Decimal(str(round(float(v), 6))) if v is not None else None

    if existing:
        existing.outcome = outcome_data["outcome"]
        existing.outcome_date = outcome_data["outcome_date"]
        existing.holding_days = outcome_data["holding_days"]
        existing.actual_exit_price = Decimal(str(entry.exit)) if entry.exit else None
        existing.target_price = target
        existing.stop_price = stop
        existing.max_adverse_excursion = _dec(outcome_data["max_adverse_excursion"])
        existing.max_favorable_excursion = _dec(outcome_data["max_favorable_excursion"])
        existing.data_source = data_source
        existing.checked_at = datetime.utcnow()
        return existing

    outcome_row = JournalOutcome(
        journal_entry_id=entry.id,
        user_id=entry.user_id,
        strategy_card_id=entry.strategy_card_id,
        symbol=entry.symbol,
        action=entry.action,
        entry_price=entry.entry,
        target_price=target,
        stop_price=stop,
        actual_exit_price=Decimal(str(entry.exit)) if entry.exit else None,
        trade_date=trade_date,
        outcome=outcome_data["outcome"],
        outcome_date=outcome_data["outcome_date"],
        holding_days=outcome_data["holding_days"],
        max_adverse_excursion=_dec(outcome_data["max_adverse_excursion"]),
        max_favorable_excursion=_dec(outcome_data["max_favorable_excursion"]),
        data_source=data_source,
        window_days=window_days,
    )
    db.add(outcome_row)
    return outcome_row


def _score_from_exit(
    action: str,
    entry: float,
    exit_price: float,
    target: float,
    stop: float,
    trade_date: date | None,
) -> dict:
    """
    Score a trade using the recorded exit price against target/stop levels.
    Returns outcome dict in the same shape as _check_bars.
    """
    if action == "long":
        pnl_direction = exit_price >= entry
        hit_target = exit_price >= target
        hit_stop = exit_price <= stop
        mae = max(0.0, entry - exit_price)
        mfe = max(0.0, exit_price - entry)
    else:  # short
        hit_target = exit_price <= target
        hit_stop = exit_price >= stop
        mae = max(0.0, exit_price - entry)
        mfe = max(0.0, entry - exit_price)

    if hit_target:
        outcome = "won"
    elif hit_stop:
        outcome = "lost"
    else:
        outcome = "inconclusive"  # closed between stop and target

    return {
        "outcome": outcome,
        "outcome_date": trade_date,
        "holding_days": None,
        "max_adverse_excursion": Decimal(str(round(mae, 6))),
        "max_favorable_excursion": Decimal(str(round(mfe, 6))),
    }


async def run_journal_validation(db: AsyncSession, user_id: uuid.UUID) -> int:
    """
    Score all journal entries for a user that have a linked broker_order.
    Returns count of entries scored.
    """
    market = MarketDataConnector()
    window = settings.VALIDATION_WINDOW_DAYS

    result = await db.execute(
        select(JournalEntry).where(JournalEntry.user_id == user_id)
    )
    entries = result.scalars().all()

    scored = 0
    for entry in entries:
        try:
            await score_journal_entry(db, entry, market, window)
            scored += 1
        except Exception as e:
            log.warning("Journal scoring failed for entry %s: %s", entry.id, e)

    await db.commit()
    log.info("Journal validation complete: %d entries for user %s", scored, user_id)
    return scored


async def get_execution_stats(
    db: AsyncSession,
    user_id: uuid.UUID,
    strategy_card_id: uuid.UUID,
) -> dict:
    """
    Return personal execution stats for one strategy card.
    Used by the strategy card detail teaching view to compare channel vs user outcomes.
    """
    entries_result = await db.execute(
        select(JournalEntry, JournalOutcome)
        .outerjoin(JournalOutcome, JournalOutcome.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.user_id == user_id,
            JournalEntry.strategy_card_id == strategy_card_id,
        )
        .order_by(JournalEntry.timestamp.desc())
    )
    rows = entries_result.all()

    total = len(rows)
    won = lost = open_ = expired = inconclusive = 0
    entries_out = []

    for entry, outcome in rows:
        o_str = outcome.outcome if outcome else "inconclusive"
        if o_str == "won":           won += 1
        elif o_str == "lost":        lost += 1
        elif o_str == "open":        open_ += 1
        elif o_str == "expired":     expired += 1
        else:                        inconclusive += 1

        entries_out.append({
            "id": str(entry.id),
            "symbol": entry.symbol,
            "action": entry.action,
            "entry": str(entry.entry),
            "exit": str(entry.exit) if entry.exit else None,
            "size": str(entry.size),
            "mode": entry.mode,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "outcome": o_str,
            "target_price": str(outcome.target_price) if outcome and outcome.target_price else None,
            "stop_price": str(outcome.stop_price) if outcome and outcome.stop_price else None,
            "actual_exit_price": str(outcome.actual_exit_price) if outcome and outcome.actual_exit_price else None,
            "max_adverse_excursion": str(outcome.max_adverse_excursion) if outcome and outcome.max_adverse_excursion else None,
            "max_favorable_excursion": str(outcome.max_favorable_excursion) if outcome and outcome.max_favorable_excursion else None,
        })

    concluded = won + lost
    win_rate = round(won / concluded, 4) if concluded > 0 else None

    return {
        "total": total,
        "won": won,
        "lost": lost,
        "open": open_,
        "expired": expired,
        "inconclusive": inconclusive,
        "win_rate": win_rate,
        "entries": entries_out,
    }
