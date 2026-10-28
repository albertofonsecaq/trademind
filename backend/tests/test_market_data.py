"""
What the yfinance fetch does when Yahoo gives it nothing — #82.

The pinned yfinance had stopped understanding Yahoo's responses, so every
fetch came back empty. An outcome check with no bars leaves a trade idea still
open, so the
run "succeeded", no strategy card ever got a win rate, and the log said the same
`possibly delisted` for every symbol whether Yahoo was broken or had simply
never heard of it.

An empty fetch has three causes that need telling apart in the log:

- Yahoo does not know the symbol (`BTCUSD`, where it says `BTC-USD`): warned,
  because every idea on that symbol will stay `open` until someone acts;
- Yahoo knows it but has no bars in the range yet — an idea from today: not a
  problem at all, so not a warning;
- anything else — Yahoo or the network failing: warned as a provider failure,
  which is what this ticket would have looked like.

Offline: yfinance's Ticker is faked, nothing reaches Yahoo.
"""
import logging
from datetime import date, timedelta

import pandas as pd
import pytest
import yfinance as yf
from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

from app.connectors import market_data
from app.core.clock import now_utc
from app.connectors.market_data import _fetch_yfinance, provider_symbol

START, END = date(2026, 9, 1), date(2026, 9, 30)


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(market_data, "_CALL_INTERVAL_S", 0)


def fake_ticker(monkeypatch, *, raises=None, frame=None):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            # Only a library told not to hide its exceptions raises them.
            if raises is not None and not yf.config.debug.hide_exceptions:
                raise raises
            return frame if frame is not None else pd.DataFrame()

    monkeypatch.setattr(yf, "Ticker", FakeTicker)


def warnings_from(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == market_data.__name__ and r.levelno >= logging.WARNING]


class TestBars:
    def test_a_bar_per_day_with_prices_and_volume(self, monkeypatch):
        frame = pd.DataFrame(
            {"Open": [10.0], "High": [12.0], "Low": [9.0], "Close": [11.0], "Volume": [500]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-09-02", tz="America/New_York")]),
        )
        fake_ticker(monkeypatch, frame=frame)

        assert _fetch_yfinance("AA", START, END) == [{
            "date": date(2026, 9, 2),
            "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 500.0,
        }]


class TestWhyThereAreNoBars:
    def test_a_symbol_the_provider_does_not_know_is_warned_as_such(self, monkeypatch, caplog):
        # yfinance's missing-timezone error is one of its missing-ticker errors:
        # the one it raises when it cannot find the symbol's metadata.
        fake_ticker(monkeypatch, raises=YFTzMissingError("BTCUSD"))

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("BTCUSD", START, END) == []

        [warning] = warnings_from(caplog)
        assert "BTCUSD" in warning
        assert "does not know" in warning

    def test_a_404_from_yahoo_is_a_symbol_it_does_not_know(self, monkeypatch, caplog):
        """
        What Yahoo actually does for `BTCUSD`: the quote lookup yfinance makes
        before fetching prices is answered 404, and that HTTP error is what
        surfaces — not yfinance's own missing-ticker error.
        """
        class NotFound(Exception):
            class response:
                status_code = 404

        fake_ticker(monkeypatch, raises=NotFound("HTTP Error 404: Quote not found for symbol: BTCUSD"))

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("BTCUSD", START, END) == []

        [warning] = warnings_from(caplog)
        assert "does not know" in warning

    def test_a_range_with_no_bars_yet_is_not_a_warning(self, monkeypatch, caplog):
        today = now_utc().date()
        fake_ticker(monkeypatch, raises=YFPricesMissingError("AA", "(1d)"))

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("AA", today, today + timedelta(days=35)) == []

        assert warnings_from(caplog) == []

    def test_a_past_range_with_no_bars_is_warned(self, monkeypatch, caplog):
        """
        Yahoo raises the same no-prices error for a symbol it knows but has no
        data for — delisted, say. Over a range that is already over, the bars
        are never coming, so the idea's outcome would stay open for good.
        """
        fake_ticker(monkeypatch, raises=YFPricesMissingError("AA", "(1d)"))

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("AA", START, END) == []

        [warning] = warnings_from(caplog)
        assert "AA" in warning
        assert "no bars" in warning

    def test_an_empty_answer_without_an_error_is_treated_the_same(self, monkeypatch, caplog):
        fake_ticker(monkeypatch, frame=pd.DataFrame())

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("AA", START, END) == []

        [warning] = warnings_from(caplog)
        assert "no bars" in warning

    def test_anything_else_is_warned_as_a_provider_failure(self, monkeypatch, caplog):
        fake_ticker(monkeypatch, raises=ValueError("Expecting value: line 1 column 1 (char 0)"))

        with caplog.at_level(logging.DEBUG):
            assert _fetch_yfinance("AA", START, END) == []

        [warning] = warnings_from(caplog)
        assert "AA" in warning
        assert "provider failed" in warning
        assert "Expecting value" in warning


class TestProviderSymbol:
    """
    A crypto pair the way sources write it, in the spelling each provider
    fetches it under — #85.

    Distillation keeps `BTCUSD`; Yahoo only knows `BTC-USD` and Polygon
    `X:BTCUSD`, so until this every crypto idea — most of the library — stayed
    open. The idea's own symbol never changes: this is the fetch only.
    """

    @pytest.mark.parametrize("symbol, yahoo", [
        ("BTCUSD", "BTC-USD"),
        ("ETHUSD", "ETH-USD"),
        ("XRPUSD", "XRP-USD"),
        ("ADAUSD", "ADA-USD"),
        ("ETHBTC", "ETH-BTC"),
        ("BTCEUR", "BTC-EUR"),
        ("DOGEUSD", "DOGE-USD"),
    ])
    def test_a_pair_is_dashed_for_yahoo(self, symbol, yahoo):
        assert provider_symbol(symbol, "yfinance") == yahoo

    @pytest.mark.parametrize("symbol, yahoo", [
        ("BTCUSDT", "BTC-USD"),
        ("DOGEUSDT", "DOGE-USD"),
        ("BTCUSDC", "BTC-USD"),
    ])
    def test_a_stablecoin_pair_is_priced_against_the_dollar_on_yahoo(self, symbol, yahoo):
        """Yahoo has no USDT or USDC pairs (404); a stablecoin tracks the dollar."""
        assert provider_symbol(symbol, "yfinance") == yahoo

    @pytest.mark.parametrize("symbol, polygon", [
        ("BTCUSD", "X:BTCUSD"),
        ("ETHBTC", "X:ETHBTC"),
        ("BTCUSDT", "X:BTCUSDT"),
    ])
    def test_a_pair_is_prefixed_for_polygon(self, symbol, polygon):
        assert provider_symbol(symbol, "polygon") == polygon

    @pytest.mark.parametrize("symbol", [
        "AA", "NVDA", "MSFT",
        "BTC",      # Yahoo's BTC is an ETF, not bitcoin: a bare base is not a pair
        "BTC-USD",  # already in Yahoo's spelling
        "YM=F", "US2Y", "SPX",
        "SEUSD",    # five letters ending in a quote currency could be a stock
        "XAUUSD", "XAGUSD",  # metals, not coins and not currencies — #115
        "USDBTC",   # a dollar priced in a coin is not forex
    ])
    def test_anything_that_is_not_a_pair_is_fetched_as_written(self, symbol):
        assert provider_symbol(symbol, "yfinance") == symbol
        assert provider_symbol(symbol, "polygon") == symbol

    async def test_the_connector_fetches_under_the_provider_symbol(self, monkeypatch):
        asked = []
        monkeypatch.setattr(market_data.settings, "POLYGON_API_KEY", "")
        monkeypatch.setattr(market_data, "_fetch_yfinance",
                            lambda symbol, start, end: asked.append(symbol) or [])

        await market_data.MarketDataConnector().get_bars("BTCUSD", START)

        assert asked == ["BTC-USD"]


class TestProviderSymbolForForex:
    """
    A forex pair the way sources write it, in the spelling each provider
    fetches it under — #114.

    Yahoo answers `EURUSD` with a 404 and only knows `EURUSD=X`; Polygon wants
    `C:EURUSD`. #85 spelled crypto pairs for each provider and left forex as
    written, so every forex idea's outcome check stayed open for want of bars.
    """

    @pytest.mark.parametrize("symbol, yahoo, polygon", [
        ("EURUSD", "EURUSD=X", "C:EURUSD"),
        ("GBPUSD", "GBPUSD=X", "C:GBPUSD"),
        ("USDJPY", "USDJPY=X", "C:USDJPY"),
        ("GBPJPY", "GBPJPY=X", "C:GBPJPY"),
        ("EURGBP", "EURGBP=X", "C:EURGBP"),
        ("AUDCAD", "AUDCAD=X", "C:AUDCAD"),
        ("NZDCHF", "NZDCHF=X", "C:NZDCHF"),
        ("USDMXN", "USDMXN=X", "C:USDMXN"),
        ("USDZAR", "USDZAR=X", "C:USDZAR"),
        ("EURTRY", "EURTRY=X", "C:EURTRY"),
    ])
    def test_a_pair_of_two_currencies_is_spelled_for_each_provider(self, symbol, yahoo, polygon):
        assert provider_symbol(symbol, "yfinance") == yahoo
        assert provider_symbol(symbol, "polygon") == polygon

    @pytest.mark.parametrize("symbol, yahoo", [("TRYUSDT", "TRY-USD"), ("BRLBTC", "BRL-BTC")])
    def test_a_minor_currency_quoted_in_a_coin_is_spelled_as_it_was(self, symbol, yahoo):
        # Only a pair of two currencies changed; one quoted in a coin resolves as
        # it did before #114, however unlikely it is to be fetched.
        assert provider_symbol(symbol, "yfinance") == yahoo

    @pytest.mark.parametrize("symbol", ["EURUSD=X", "C:EURUSD", "EUR/USD"])
    def test_one_already_spelled_some_other_way_is_left_alone(self, symbol):
        assert provider_symbol(symbol, "yfinance") == symbol
        assert provider_symbol(symbol, "polygon") == symbol

    async def test_the_connector_fetches_it_under_yahoos_spelling(self, monkeypatch):
        asked = []
        monkeypatch.setattr(market_data.settings, "POLYGON_API_KEY", "")
        monkeypatch.setattr(market_data, "_fetch_yfinance",
                            lambda symbol, start, end: asked.append(symbol) or [])

        await market_data.MarketDataConnector().get_bars("EURUSD", START)

        assert asked == ["EURUSD=X"]

