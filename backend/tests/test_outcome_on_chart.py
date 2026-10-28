"""
A trade idea's chart shows how its outcome check resolved it — #112.

The outcome badge says won or lost; the chart has to tell the same story: the
day the target or stop was hit, the window the check looked at, and how far
price went against and for the trade on the way. The window is the check's
own, so changing the validation setting later cannot make the two disagree.

Offline: the market data and the outcome check are answered locally.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.connectors.market_data import PriceSource
from app.services import trade_idea_chart

PROPOSED = datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc)


def _idea(**overrides):
    fields = dict(
        symbol="EURUSD", action="long", message_timestamp=PROPOSED,
        entry=Decimal("1.0850"), target=Decimal("1.0950"), stop=Decimal("1.0800"),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _check(**overrides):
    fields = dict(
        outcome="won", outcome_date=date(2026, 9, 22), trade_date=date(2026, 9, 15), window_days=20,
        max_adverse_excursion=Decimal("0.0030"), max_favorable_excursion=Decimal("0.0100"),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMarket:
    def __init__(self):
        self.asked: list[tuple] = []

    def price_source(self, symbol):
        return PriceSource(priced_as=symbol, stands_in_for_spot=False)

    async def get_bars(self, symbol, start, days=None):
        self.asked.append((symbol, start, days))
        return []


class TestTheWindow:
    async def test_is_the_one_the_outcome_check_looked_at(self):
        # Checked with a 20-day window: the chart covers those 20 days, whatever
        # the setting says today.
        market = FakeMarket()

        chart = await trade_idea_chart.chart_of(_idea(), market, _check())

        assert market.asked == [("EURUSD", date(2026, 8, 16), trade_idea_chart.DAYS_BEFORE + 20)]
        assert (chart.outcome.window_start, chart.outcome.window_end) == (date(2026, 9, 15), date(2026, 10, 5))


class TestHowFarPriceWent:
    async def test_against_a_long_is_below_its_entry_and_for_it_above(self):
        # Entry 1.0850, 30 pips against at worst, 100 for at best.
        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket(), _check())

        assert (chart.outcome.worst_price, chart.outcome.best_price) == (Decimal("1.0820"), Decimal("1.0950"))

    async def test_against_a_short_is_above_its_entry_and_for_it_below(self):
        idea = _idea(action="short", target=Decimal("1.0750"), stop=Decimal("1.0900"))

        chart = await trade_idea_chart.chart_of(idea, FakeMarket(), _check())

        assert (chart.outcome.worst_price, chart.outcome.best_price) == (Decimal("1.0880"), Decimal("1.0750"))

    async def test_is_unknown_when_the_check_did_not_measure_it(self):
        check = _check(max_adverse_excursion=None, max_favorable_excursion=None)

        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket(), check)

        assert (chart.outcome.worst_price, chart.outcome.best_price) == (None, None)


class TestNoResolution:
    async def test_for_an_idea_never_checked_says_so(self):
        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket(), None)

        assert chart.outcome is None
        assert chart.no_outcome_because == "not_checked"

    async def test_for_an_inconclusive_check_says_so_and_draws_no_resolution(self):
        # Inconclusive is a check that had too little to go on — missing levels,
        # usually — so there is no outcome date or window worth drawing.
        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket(), _check(outcome="inconclusive", outcome_date=None))

        assert chart.outcome is None
        assert chart.no_outcome_because == "inconclusive"

    async def test_for_a_resolved_check_there_is_no_reason_to_give(self):
        chart = await trade_idea_chart.chart_of(_idea(), FakeMarket(), _check())

        assert chart.no_outcome_because is None
