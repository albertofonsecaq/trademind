"""
Trade ideas API — an idea as a member looks at it, beyond its text.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analysis_runs import assert_member
from app.connectors.market_data import MarketDataConnector
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.outcome_check import OutcomeCheck
from app.models.trade_idea import TradeIdea
from app.models.user import User
from app.services.trade_idea_chart import chart_of

router = APIRouter(prefix="/workspaces", tags=["trade-ideas"])


class CandleOut(BaseModel):
    time: str           # YYYY-MM-DD, the shape a daily chart takes it in
    open: float
    high: float
    low: float
    close: float


class ChartedOutcomeOut(BaseModel):
    outcome: str
    outcome_date: str | None    # YYYY-MM-DD
    window_start: str
    window_end: str
    worst_price: float | None
    best_price: float | None


class TradeIdeaChartOut(BaseModel):
    symbol: str
    action: str | None
    proposed_on: str    # YYYY-MM-DD
    entry: float | None
    target: float | None
    stop: float | None
    candles: list[CandleOut]
    priced_as: str
    stands_in_for_spot: bool
    outcome: ChartedOutcomeOut | None
    no_outcome_because: str | None      # not_checked | inconclusive


def get_market() -> MarketDataConnector:
    return MarketDataConnector()


def _number(value) -> float | None:
    return float(value) if value is not None else None


@router.get("/{workspace_id}/trade-ideas/{idea_id}/chart", response_model=TradeIdeaChartOut)
async def get_trade_idea_chart(
    workspace_id: uuid.UUID,
    idea_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    market: MarketDataConnector = Depends(get_market),
) -> TradeIdeaChartOut:
    """A trade idea in daily bars around the day it was proposed, with its levels (#109) and outcome (#112)."""
    await assert_member(db, workspace_id, current_user.id)

    idea = (await db.execute(
        select(TradeIdea).where(TradeIdea.id == idea_id, TradeIdea.workspace_id == workspace_id)
    )).scalar_one_or_none()
    check = (await db.execute(
        select(OutcomeCheck).where(OutcomeCheck.trade_idea_id == idea_id)
    )).scalar_one_or_none() if idea is not None else None
    chart = await chart_of(idea, market, check) if idea is not None else None
    if chart is None:
        raise HTTPException(status_code=404, detail="No chart for this trade idea")

    return TradeIdeaChartOut(
        symbol=chart.symbol,
        action=chart.action,
        proposed_on=chart.proposed_on.isoformat(),
        entry=_number(chart.entry),
        target=_number(chart.target),
        stop=_number(chart.stop),
        candles=[
            CandleOut(time=c.day.isoformat(), open=c.open, high=c.high, low=c.low, close=c.close)
            for c in chart.candles
        ],
        priced_as=chart.priced_as,
        stands_in_for_spot=chart.stands_in_for_spot,
        outcome=_outcome_out(chart.outcome) if chart.outcome else None,
        no_outcome_because=chart.no_outcome_because,
    )


def _outcome_out(outcome) -> ChartedOutcomeOut:
    return ChartedOutcomeOut(
        outcome=outcome.outcome,
        outcome_date=outcome.outcome_date.isoformat() if outcome.outcome_date else None,
        window_start=outcome.window_start.isoformat(),
        window_end=outcome.window_end.isoformat(),
        worst_price=_number(outcome.worst_price),
        best_price=_number(outcome.best_price),
    )
