"""
A trade idea drawn against what price did around it (#109).

The idea's symbol in daily bars, from a month before it was proposed to the end
of the window its outcome check looks at, with the levels it was proposed at
and how that check resolved it (#112). With a check, the window is the check's
own, so the chart shows the days its outcome was decided on; without one, it is
the window validation would use today.
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
    """The idea's outcome check, in the chart's terms (#112)."""
    outcome: str                    # won | lost | open | expired | inconclusive
    outcome_date: date | None       # the day the target or stop was hit, if one was
    window_start: date              # the days the check looked at
    window_end: date
    # The adverse and favorable excursions as prices: how far price went
    # against the trade and for it, before it resolved.
    adverse_price: Decimal | None
    favorable_price: Decimal | None


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
    outcome: ChartedOutcome | None = None     # None: never outcome-checked


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
    window_end = window_start + timedelta(days=window_days)
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
            window_end=window_end,
            adverse_price=_away_from(check, check.max_adverse_excursion, against=True),
            favorable_price=_away_from(check, check.max_favorable_excursion, against=False),
        ) if check else None,
    )


def _away_from(check, excursion: Decimal | None, *, against: bool) -> Decimal | None:
    """
    The price `excursion` from the check's entry, against the trade or for it.

    An outcome check measures excursions as distances from the entry it used,
    which is its own, not whatever the idea says now; against a long is below
    it, against a short above it.
    """
    if excursion is None or check.entry_price is None:
        return None
    downward = against == (check.action == "long")
    return check.entry_price - excursion if downward else check.entry_price + excursion
