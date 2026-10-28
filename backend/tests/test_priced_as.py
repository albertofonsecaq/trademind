"""
What an outcome check and a chart were priced against — #115.

Gold and silver have no spot price on Yahoo, so their futures stand in. That
is a choice a reader of a result has to be able to see: an outcome check
records the instrument it was decided against, and a chart says when its
prices are a stand-in.

Offline: the market data is answered locally, nothing reaches a provider.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.connectors import market_data
from app.connectors.market_data import PriceSource, MarketDataConnector
from app.models.outcome_check import OutcomeCheck
from app.api.validation import OutcomeCheckOut
from app.services import trade_idea_chart, validation_service
from tests.conftest import FakeSession

PROPOSED = datetime(2026, 9, 15, 13, 30, tzinfo=timezone.utc)


def _gold_idea():
    return SimpleNamespace(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), symbol="XAUUSD", action="long",
        message_timestamp=PROPOSED,
        entry=Decimal("2650"), target=Decimal("2700"), stop=Decimal("2620"),
    )


class FakeMarket:
    """Prices gold from its futures, as Yahoo does, with the bars a test gives it."""

    def __init__(self, bars: list[dict] | None = None):
        self.bars = bars or []

    def data_source_name(self):
        return "yfinance"

    def price_source(self, symbol):
        return PriceSource(priced_as="GC=F", stands_in_for_spot=True)

    async def get_bars(self, symbol, start, days=None):
        return self.bars


class TestTheConnector:
    @pytest.fixture
    def on(self, monkeypatch):
        def _provider(polygon_key):
            monkeypatch.setattr(market_data.settings, "POLYGON_API_KEY", polygon_key)
            return MarketDataConnector()
        return _provider

    def test_says_gold_on_yahoo_is_its_futures_standing_in(self, on):
        assert on("").price_source("XAUUSD") == PriceSource(priced_as="GC=F", stands_in_for_spot=True)

    def test_says_gold_on_polygon_is_spot(self, on):
        assert on("key").price_source("XAUUSD") == PriceSource(priced_as="C:XAUUSD", stands_in_for_spot=False)

    def test_says_a_coin_is_itself_under_another_spelling(self, on):
        assert on("").price_source("BTCUSD") == PriceSource(priced_as="BTC-USD", stands_in_for_spot=False)


class TestAnOutcomeCheck:
    async def test_records_the_instrument_it_was_decided_against(self):
        session = FakeSession()            # no earlier check of this idea

        check = await validation_service.check_idea_outcome(session, _gold_idea(), FakeMarket(), 30)

        assert check.priced_as == "GC=F"
        assert check in session.added

    async def test_on_gold_resolves_on_the_futures_bars(self):
        # The day after it was proposed, gold futures traded through the target.
        bars = [
            {"date": date(2026, 9, 15), "open": 2652, "high": 2660, "low": 2640, "close": 2655, "volume": 0},
            {"date": date(2026, 9, 16), "open": 2655, "high": 2710, "low": 2650, "close": 2705, "volume": 0},
        ]

        check = await validation_service.check_idea_outcome(FakeSession(), _gold_idea(), FakeMarket(bars), 30)

        assert (check.outcome, check.outcome_date, check.priced_as) == ("won", date(2026, 9, 16), "GC=F")

    def test_is_served_with_what_it_was_priced_as(self):
        check = OutcomeCheck(
            id=uuid.uuid4(), trade_idea_id=uuid.uuid4(), symbol="XAUUSD", action="long",
            trade_date=date(2026, 9, 15), outcome="won", data_source="yfinance",
            priced_as="GC=F", checked_at=PROPOSED, window_days=30,
        )

        assert OutcomeCheckOut.model_validate(check).priced_as == "GC=F"


class TestAChart:
    async def test_says_what_it_is_priced_as_and_that_it_stands_in_for_spot(self):
        chart = await trade_idea_chart.chart_of(_gold_idea(), FakeMarket())

        assert chart.priced_as == "GC=F"
        assert chart.stands_in_for_spot is True
