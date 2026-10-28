"""
A trade idea drawn against what price did around it (#109).

The idea's symbol in daily bars, from a month before it was proposed to the end
of the window its outcome check looks at, with the levels it was proposed at.
The window comes from the same setting validation uses, so the chart shows the
days its outcome was decided on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.config import settings

# How much of what came before an idea the chart shows, for context.
DAYS_BEFORE = 30

# A trade has a direction; an idea that only informs or comments, or whose
# direction distillation could not tell, has no levels to draw and is never
# outcome-checked either.
_DIRECTIONS = ("long", "short")


@dataclass(frozen=True)
class Candle:
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ChartedOutcome:
    """How the idea's outcome check resolved it, in the chart's terms (#112)."""
    outcome: str                    # won | lost | open | expired
    outcome_date: date | None       # the day the target or stop was hit
    window_start: date              # the days the check looked at
    window_end: date
    worst_price: Decimal | None     # furthest price went against the trade
    best_price: Decimal | None      # and for it, before it resolved


@dataclass(frozen=True)
class IdeaChart:
    symbol: str
    action: str | None
    proposed_on: date
    entry: Decimal | None
    target: Decimal | None
    stop: Decimal | None
    candles: list[Candle]
    priced_as: str              # the provider's own symbol the candles are (#115)
    stands_in_for_spot: bool    # futures drawn in place of a spot price
    outcome: ChartedOutcome | None = None
    # Why there is no outcome to draw: "not_checked" or "inconclusive".
    no_outcome_because: str | None = None


def can_be_charted(idea) -> bool:
    """A trade on a symbol, on a day: what a chart can place."""
    return bool(idea.symbol) and idea.message_timestamp is not None and idea.action in _DIRECTIONS


async def chart_of(idea, market, check=None) -> IdeaChart | None:
    """
    The idea's chart, or None for one that cannot be charted.

    With its outcome check, the window is the check's own: the days its outcome
    was decided on, whatever the validation setting is today (#112).
    """
    if not can_be_charted(idea):
        return None
    proposed_on = idea.message_timestamp.date()
    window_start = check.trade_date if check and check.trade_date else proposed_on
    window_days = check.window_days if check else settings.VALIDATION_WINDOW_DAYS
    resolved = check is not None and check.outcome != "inconclusive"
    source = market.price_source(idea.symbol)
    bars = await market.get_bars(
        idea.symbol,
        window_start - timedelta(days=DAYS_BEFORE),
        DAYS_BEFORE + window_days,
    )
    return IdeaChart(
        symbol=idea.symbol,
        action=idea.action,
        proposed_on=proposed_on,
        entry=idea.entry,
        target=idea.target,
        stop=idea.stop,
        candles=[
            Candle(day=b["date"], open=b["open"], high=b["high"], low=b["low"], close=b["close"])
            for b in bars
        ],
        priced_as=source.priced_as,
        stands_in_for_spot=source.stands_in_for_spot,
        outcome=ChartedOutcome(
            outcome=check.outcome,
            outcome_date=check.outcome_date,
            window_start=window_start,
            window_end=window_start + timedelta(days=window_days),
            worst_price=_away_from(idea, check.max_adverse_excursion, against=True),
            best_price=_away_from(idea, check.max_favorable_excursion, against=False),
        ) if resolved else None,
        no_outcome_because=None if resolved else ("inconclusive" if check else "not_checked"),
    )


def _away_from(idea, excursion: Decimal | None, *, against: bool) -> Decimal | None:
    """
    The price `excursion` from the idea's entry, against the trade or for it.

    An outcome check measures excursions as distances from entry; against a
    long is below it, against a short above it.
    """
    if excursion is None or idea.entry is None:
        return None
    downward = against == (idea.action == "long")
    return idea.entry - excursion if downward else idea.entry + excursion
