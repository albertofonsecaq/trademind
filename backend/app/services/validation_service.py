"""
Validation service — backtests trade_ideas against real market price data.

Per-idea flow:
  1. Skip if missing entry/target/stop/symbol (→ inconclusive)
  2. Fetch OHLCV bars from signal date for VALIDATION_WINDOW_DAYS
  3. Scan bars day-by-day: target hit before stop → won; stop hit first → lost
  4. Window elapsed without resolution → expired; still in window → open
  5. Store/update OutcomeCheck row

Per-strategy-card aggregation:
  - Recency-weighted win rate (exponential decay, half-life = VALIDATION_DECAY_HALF_LIFE_DAYS)
  - Wilson 95% confidence interval on the win rate
  - Walk-forward validation: chronological 60/40 split, flag if degradation > 15pp
  - Update strategy_card.win_rate, confidence_interval, walk_forward_result
"""
from __future__ import annotations
import logging
import math
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.connectors.market_data import MarketDataConnector
from app.models.trade_idea import TradeIdea
from app.models.strategy_card import StrategyCard
from app.models.outcome_check import OutcomeCheck
from app.services.confidence_tier import compute_confidence_tier

log = logging.getLogger(__name__)

_DECAY_RATE = math.log(2) / settings.VALIDATION_DECAY_HALF_LIFE_DAYS
_OVERFITTING_THRESHOLD = 0.15   # flag if out-of-sample win rate drops by > 15pp
_WALK_FORWARD_SPLIT = 0.60      # 60% in-sample, 40% out-of-sample
_MIN_WALK_FORWARD_SAMPLES = 3   # need at least 3 per group to run walk-forward


# ── stats helpers ──────────────────────────────────────────────────────────────

def wilson_ci(p: float, n: int, z: float = 1.96) -> dict:
    """95% Wilson score confidence interval for a proportion."""
    if n == 0:
        return {"lower": 0.0, "upper": 1.0, "confidence_level": 0.95, "n": 0}
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)
    return {
        "lower": round(max(0.0, center - margin), 4),
        "upper": round(min(1.0, center + margin), 4),
        "confidence_level": 0.95,
        "n": n,
    }


def recency_weight(trade_date: date | None) -> float:
    """Exponential decay weight — recent ideas count more."""
    if not trade_date:
        return 0.5
    days_ago = (date.today() - trade_date).days
    return math.exp(-_DECAY_RATE * days_ago)


def walk_forward_check(concluded: list[tuple]) -> dict:
    """
    concluded: list of (trade_date, outcome_str) sorted oldest-first.
    Returns walk_forward_result dict.
    """
    n = len(concluded)
    split_at = int(n * _WALK_FORWARD_SPLIT)
    in_group  = concluded[:split_at]
    out_group = concluded[split_at:]

    if len(in_group) < _MIN_WALK_FORWARD_SAMPLES or len(out_group) < _MIN_WALK_FORWARD_SAMPLES:
        return {"sufficient_data": False, "reason": f"need {_MIN_WALK_FORWARD_SAMPLES}+ per group"}

    in_wr  = sum(1 for _, o in in_group  if o == "won") / len(in_group)
    out_wr = sum(1 for _, o in out_group if o == "won") / len(out_group)
    degradation = in_wr - out_wr

    return {
        "sufficient_data": True,
        "in_sample_n": len(in_group),
        "out_sample_n": len(out_group),
        "in_sample_win_rate":  round(in_wr, 4),
        "out_sample_win_rate": round(out_wr, 4),
        "performance_degradation": round(degradation, 4),
        "potentially_overfit": degradation > _OVERFITTING_THRESHOLD,
    }


# ── outcome checking ───────────────────────────────────────────────────────────

def _check_bars(
    bars: list[dict],
    action: str,
    entry: float,
    target: float,
    stop: float,
    trade_date: date,
    window_days: int,
) -> dict:
    """
    Scan OHLCV bars and determine outcome.
    Returns dict with outcome, outcome_date, holding_days, mae, mfe.
    """
    cutoff = trade_date + timedelta(days=window_days)
    mae = 0.0  # max adverse excursion (worst case against the trade)
    mfe = 0.0  # max favorable excursion (best case for the trade)

    for bar in bars:
        if bar["date"] > cutoff:
            break
        high, low = bar["high"], bar["low"]

        if action == "long":
            favorable_move  = high - entry
            adverse_move    = entry - low
            if high >= target:
                return {
                    "outcome": "won", "outcome_date": bar["date"],
                    "holding_days": (bar["date"] - trade_date).days,
                    "max_adverse_excursion": Decimal(str(round(max(mae, 0), 6))),
                    "max_favorable_excursion": Decimal(str(round(max(mfe, favorable_move), 6))),
                }
            if low <= stop:
                return {
                    "outcome": "lost", "outcome_date": bar["date"],
                    "holding_days": (bar["date"] - trade_date).days,
                    "max_adverse_excursion": Decimal(str(round(max(mae, adverse_move), 6))),
                    "max_favorable_excursion": Decimal(str(round(max(mfe, 0), 6))),
                }
        elif action == "short":
            favorable_move  = entry - low
            adverse_move    = high - entry
            if low <= target:
                return {
                    "outcome": "won", "outcome_date": bar["date"],
                    "holding_days": (bar["date"] - trade_date).days,
                    "max_adverse_excursion": Decimal(str(round(max(mae, 0), 6))),
                    "max_favorable_excursion": Decimal(str(round(max(mfe, favorable_move), 6))),
                }
            if high >= stop:
                return {
                    "outcome": "lost", "outcome_date": bar["date"],
                    "holding_days": (bar["date"] - trade_date).days,
                    "max_adverse_excursion": Decimal(str(round(max(mae, adverse_move), 6))),
                    "max_favorable_excursion": Decimal(str(round(max(mfe, 0), 6))),
                }

        # Update running MAE/MFE
        if action == "long":
            mfe = max(mfe, high - entry)
            mae = max(mae, entry - low)
        else:
            mfe = max(mfe, entry - low)
            mae = max(mae, high - entry)

    today = date.today()
    if trade_date + timedelta(days=window_days) > today:
        # Window not yet elapsed
        return {"outcome": "open", "outcome_date": None, "holding_days": None,
                "max_adverse_excursion": Decimal(str(round(mae, 6))),
                "max_favorable_excursion": Decimal(str(round(mfe, 6)))}

    return {"outcome": "expired", "outcome_date": None, "holding_days": window_days,
            "max_adverse_excursion": Decimal(str(round(mae, 6))),
            "max_favorable_excursion": Decimal(str(round(mfe, 6)))}


async def check_idea_outcome(
    db: AsyncSession,
    idea: TradeIdea,
    market: MarketDataConnector,
    window_days: int,
) -> OutcomeCheck:
    """
    Check or re-check the outcome for one trade_idea. Upserts OutcomeCheck.
    Returns the OutcomeCheck row.
    """
    # Skip ideas that are still "open" and were checked recently (within 24h)
    existing_result = await db.execute(
        select(OutcomeCheck).where(OutcomeCheck.trade_idea_id == idea.id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing and existing.outcome in ("won", "lost", "expired", "inconclusive"):
        return existing  # final states don't need re-checking

    # Validate required fields
    if not all([idea.symbol, idea.action, idea.entry, idea.target, idea.stop, idea.message_timestamp]):
        outcome_data = {"outcome": "inconclusive", "outcome_date": None, "holding_days": None,
                        "max_adverse_excursion": None, "max_favorable_excursion": None}
    else:
        trade_date = idea.message_timestamp.date()
        bars = await market.get_bars(idea.symbol, trade_date, window_days)

        if not bars:
            outcome_data = {"outcome": "open", "outcome_date": None, "holding_days": None,
                            "max_adverse_excursion": None, "max_favorable_excursion": None}
        else:
            outcome_data = _check_bars(
                bars=bars,
                action=idea.action,
                entry=float(idea.entry),
                target=float(idea.target),
                stop=float(idea.stop),
                trade_date=trade_date,
                window_days=window_days,
            )

    if existing:
        existing.outcome                  = outcome_data["outcome"]
        existing.outcome_date             = outcome_data["outcome_date"]
        existing.holding_days             = outcome_data["holding_days"]
        existing.max_adverse_excursion    = outcome_data["max_adverse_excursion"]
        existing.max_favorable_excursion  = outcome_data["max_favorable_excursion"]
        existing.checked_at               = datetime.utcnow()
        existing.data_source              = market.data_source_name()
        return existing

    check = OutcomeCheck(
        trade_idea_id           = idea.id,
        workspace_id            = idea.workspace_id,
        symbol                  = idea.symbol,
        entry_price             = idea.entry,
        target_price            = idea.target,
        stop_price              = idea.stop,
        action                  = idea.action,
        trade_date              = idea.message_timestamp.date() if idea.message_timestamp else None,
        outcome                 = outcome_data["outcome"],
        outcome_date            = outcome_data["outcome_date"],
        holding_days            = outcome_data["holding_days"],
        max_adverse_excursion   = outcome_data["max_adverse_excursion"],
        max_favorable_excursion = outcome_data["max_favorable_excursion"],
        data_source             = market.data_source_name(),
        window_days             = window_days,
    )
    db.add(check)
    return check


# ── strategy card aggregation ─────────────────────────────────────────────────

async def validate_strategy_card(
    db: AsyncSession,
    card: StrategyCard,
    market: MarketDataConnector,
    window_days: int,
) -> None:
    """
    Run outcome checks for all trade_ideas in a card's supporting_evidence,
    then compute and persist win_rate + CI + walk_forward_result.
    """
    if not card.supporting_evidence:
        return

    idea_ids = [uuid.UUID(i) for i in card.supporting_evidence if i]
    result = await db.execute(select(TradeIdea).where(TradeIdea.id.in_(idea_ids)))
    ideas = result.scalars().all()

    checks: list[OutcomeCheck] = []
    for idea in ideas:
        try:
            check = await check_idea_outcome(db, idea, market, window_days)
            checks.append(check)
        except Exception as e:
            log.warning("Outcome check failed for idea %s: %s", idea.id, e)

    await db.flush()

    # Gather concluded outcomes for stats
    concluded = [
        (c.trade_date, c.outcome)
        for c in checks
        if c.outcome in ("won", "lost") and c.trade_date
    ]

    if not concluded:
        card.win_rate = None
        card.confidence_interval = None
        card.walk_forward_result = walk_forward_check(concluded)
        card.confidence_tier = compute_confidence_tier(card.sample_size, None, None)
        card.validation_updated_at = datetime.utcnow()
        return

    # Recency-weighted win rate
    weights = [recency_weight(td) for td, _ in concluded]
    binary  = [1.0 if o == "won" else 0.0 for _, o in concluded]
    total_w = sum(weights)
    weighted_p = sum(w * b for w, b in zip(weights, binary)) / total_w

    prev_win_rate = card.win_rate
    prev_tier = card.confidence_tier or "still_learning"

    card.win_rate = Decimal(str(round(weighted_p, 4)))
    card.confidence_interval = wilson_ci(weighted_p, len(concluded))

    # Walk-forward on the full concluded set, sorted chronologically
    concluded_sorted = sorted(concluded, key=lambda x: x[0])
    card.walk_forward_result = walk_forward_check(concluded_sorted)

    new_tier = compute_confidence_tier(card.sample_size, card.win_rate, card.confidence_interval)
    card.confidence_tier = new_tier
    card.validation_updated_at = datetime.utcnow()

    # Add a version snapshot if win_rate or tier changed meaningfully
    wr_changed = prev_win_rate is None or abs(float(prev_win_rate) - weighted_p) > 0.02
    tier_changed = prev_tier != new_tier
    if wr_changed or tier_changed:
        changes_parts = []
        if wr_changed:
            old_wr = f"{round(float(prev_win_rate)*100)}%" if prev_win_rate is not None else "none"
            changes_parts.append(f"win rate {old_wr} → {round(weighted_p*100)}% (n={len(concluded)})")
        if tier_changed:
            changes_parts.append(f"tier {prev_tier} → {new_tier}")
        snapshot = {
            "version": card.version,
            "snapshot_at": datetime.utcnow().isoformat(),
            "change_source": "validation",
            "sample_size": card.sample_size,
            "win_rate": str(card.win_rate),
            "confidence_tier": new_tier,
            "changes_en": "; ".join(changes_parts),
        }
        card.version_history = (card.version_history or []) + [snapshot]
        card.version += 1


# ── workspace-level runner ────────────────────────────────────────────────────

async def run_validation(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    """
    Validate all strategy cards in a workspace.
    Returns count of cards processed.
    """
    market = MarketDataConnector()
    window = settings.VALIDATION_WINDOW_DAYS

    result = await db.execute(
        select(StrategyCard).where(StrategyCard.workspace_id == workspace_id)
    )
    cards = result.scalars().all()

    for card in cards:
        try:
            await validate_strategy_card(db, card, market, window)
        except Exception as e:
            log.error("Validation failed for card %s: %s", card.id, e)

    await db.commit()
    log.info("Validation complete: %d cards for workspace %s", len(cards), workspace_id)
    return len(cards)
