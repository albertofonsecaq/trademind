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
from app.connectors.market_data import _fetch_yfinance

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
