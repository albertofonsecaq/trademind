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
class IdeaChart:
    symbol: str
    action: str | None
    proposed_on: date
    entry: Decimal | None
    target: Decimal | None
    stop: Decimal | None
    candles: list[Candle]


def can_be_charted(idea) -> bool:
    """A trade on a symbol, on a day: what a chart can place."""
    return bool(idea.symbol) and idea.message_timestamp is not None and idea.action in _DIRECTIONS


async def chart_of(idea, market) -> IdeaChart | None:
    """The idea's chart, or None for one that cannot be charted."""
    if not can_be_charted(idea):
        return None
    proposed_on = idea.message_timestamp.date()
    bars = await market.get_bars(
        idea.symbol,
        proposed_on - timedelta(days=DAYS_BEFORE),
        DAYS_BEFORE + settings.VALIDATION_WINDOW_DAYS,
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
    )
