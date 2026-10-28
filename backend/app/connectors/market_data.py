"""
Market data connector — provides OHLCV bars for outcome validation.
Default: yfinance (free, no API key, good historical coverage).
Upgrade: Polygon.io (set POLYGON_API_KEY for higher rate limits and real-time data).

This is NOT a BaseConnector subclass — market data is used for validation,
not for the content-ingestion pipeline.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import date, timedelta
from functools import lru_cache

from app.core.clock import now_utc
from app.core.config import settings

log = logging.getLogger(__name__)

# Rate-limit guard: wait between consecutive API calls (yfinance can throttle)
_CALL_INTERVAL_S = 0.5

# What a crypto pair can be quoted in, longest first so USDT is not read as USD.
_QUOTE_CURRENCIES = ("USDT", "USDC", "USD", "EUR", "BTC", "ETH")
# Yahoo has no stablecoin pairs; a stablecoin tracks the dollar closely enough
# to tell whether a target or a stop was hit first.
_YAHOO_QUOTE = {"USDT": "USD", "USDC": "USD"}
# The currencies a forex pair is made of: the majors, and the other currencies
# sources quote against them. Two side by side, EURUSD, is a pair Yahoo spells
# EURUSD=X and Polygon C:EURUSD (#114).
_FIAT = frozenset({
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR", "MXN",
    "SGD", "HKD", "CNH", "CNY", "INR", "KRW", "BRL", "ILS", "THB",
})
# Fiat currencies and metals quoted in a coin are not crypto pairs, and are left
# alone; metals stay as written until #115 (Yahoo has no spot gold or silver).
_NOT_CRYPTO_BASES = frozenset({"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "XAG"})
# A US stock ticker is at most five letters, so six or more ending in a quote
# currency is a pair. Five could be either (SEUSD), and is left alone.
_MIN_PAIR_LENGTH = 6


def _is_forex_pair(symbol: str) -> bool:
    """Two three-letter currency codes, base then quote: EURUSD."""
    base, quote = symbol[:3], symbol[3:]
    return len(symbol) == 6 and base in _FIAT and quote in _FIAT


def provider_symbol(symbol: str, provider: str) -> str:
    """
    The spelling `provider` fetches `symbol` under (#85).

    Sources write a crypto pair as BASE+QUOTE (BTCUSD), which is what
    distillation keeps; Yahoo knows it as BTC-USD and Polygon as X:BTCUSD.
    A forex pair (EURUSD) is EURUSD=X on Yahoo and C:EURUSD on Polygon (#114).
    Anything else, including a bare BTC (an ETF on Yahoo) and metals (XAUUSD),
    is fetched as written.
    """
    if len(symbol) < _MIN_PAIR_LENGTH or not symbol.isalpha() or not symbol.isupper():
        return symbol
    if _is_forex_pair(symbol):
        return f"C:{symbol}" if provider == "polygon" else f"{symbol}=X"
    quote = next((q for q in _QUOTE_CURRENCIES if symbol.endswith(q)), None)
    if quote is None:
        return symbol
    base = symbol[: -len(quote)]
    if base in _NOT_CRYPTO_BASES:
        return symbol
    if provider == "polygon":
        return f"X:{symbol}"
    return f"{base}-{_YAHOO_QUOTE.get(quote, quote)}"


def _is_unknown_symbol(e: Exception) -> bool:
    """
    Whether Yahoo has told yfinance it does not know the symbol at all.

    yfinance says so with its own missing-ticker error, but for a symbol like
    `BTCUSD` the 404 Yahoo answers its quote lookup with surfaces first.
    """
    from yfinance.exceptions import YFTickerMissingError

    response = getattr(e, "response", None)
    return isinstance(e, YFTickerMissingError) or getattr(response, "status_code", None) == 404


def _log_no_bars(symbol: str, start: date, end: date) -> None:
    """
    A symbol Yahoo knows, with no bars in the range. Expected while the range
    is still running — an idea from today. Once it is over the bars are never
    coming (a delisted symbol, say), and the idea's outcome stays open for good.
    """
    if end < now_utc().date():
        log.warning("yfinance has no bars for %s between %s and %s, a range that is already over",
                    symbol, start, end)
    else:
        log.debug("yfinance has no bars yet for %s between %s and %s", symbol, start, end)


def _fetch_yfinance(symbol: str, start: date, end: date) -> list[dict]:
    """
    Synchronous yfinance fetch, run in executor.

    Returns no bars for three different reasons, and logs which (#82): Yahoo
    does not know the symbol, Yahoo has no bars in the range yet, or Yahoo or
    the network failed. Until #82 all three logged the same "possibly delisted",
    which is how a broken yfinance went unnoticed.
    """
    import yfinance as yf
    from yfinance.exceptions import YFPricesMissingError

    # yfinance swallows its own errors by default and logs them as
    # "possibly delisted"; raising is what lets the cause be told apart.
    yf.config.debug.hide_exceptions = False
    try:
        time.sleep(_CALL_INTERVAL_S)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # end is exclusive in yfinance
            auto_adjust=True,
        )
    except YFPricesMissingError:
        _log_no_bars(symbol, start, end)
        return []
    except Exception as e:
        if _is_unknown_symbol(e):
            log.warning("yfinance does not know the symbol %s: %s", symbol, e)
        else:
            log.warning("yfinance provider failed for %s: %s", symbol, e)
        return []

    try:
        bars = []
        for ts, row in hist.iterrows():
            bars.append({
                "date": ts.date(),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            })
        if not bars:
            _log_no_bars(symbol, start, end)
        return bars
    except Exception as e:
        log.warning("yfinance provider failed for %s, returning bars that could not be read: %s", symbol, e)
        return []


def _fetch_polygon(symbol: str, start: date, end: date) -> list[dict]:
    """Polygon.io REST v2 aggregate bars. Only called if POLYGON_API_KEY is set."""
    import requests as req
    api_key = settings.POLYGON_API_KEY
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day"
        f"/{start.isoformat()}/{end.isoformat()}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={api_key}"
    )
    try:
        resp = req.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        bars = []
        for r in data.get("results", []):
            # Polygon timestamps are milliseconds UTC
            import datetime as dt
            bar_date = dt.datetime.utcfromtimestamp(r["t"] / 1000).date()
            bars.append({
                "date":   bar_date,
                "open":   float(r["o"]),
                "high":   float(r["h"]),
                "low":    float(r["l"]),
                "close":  float(r["c"]),
                "volume": float(r.get("v", 0)),
            })
        return bars
    except Exception as e:
        log.warning("Polygon fetch failed for %s: %s", symbol, e)
        return []


class MarketDataConnector:
    """Fetches OHLCV bars for a symbol over a date range."""

    def __init__(self):
        self._use_polygon = bool(settings.POLYGON_API_KEY)

    async def get_bars(
        self,
        symbol: str,
        start: date,
        days: int | None = None,
    ) -> list[dict]:
        """
        Return OHLCV bars for `symbol` starting on `start`.
        If `days` is given, cap to that many calendar days.
        """
        end = start + timedelta(days=(days or settings.VALIDATION_WINDOW_DAYS) + 5)

        fetched_as = provider_symbol(symbol, self.data_source_name())
        loop = asyncio.get_event_loop()
        if self._use_polygon:
            bars = await loop.run_in_executor(None, _fetch_polygon, fetched_as, start, end)
        else:
            bars = await loop.run_in_executor(None, _fetch_yfinance, fetched_as, start, end)

        # Cap to the requested window
        cutoff = start + timedelta(days=(days or settings.VALIDATION_WINDOW_DAYS))
        return [b for b in bars if b["date"] <= cutoff]

    def data_source_name(self) -> str:
        return "polygon" if self._use_polygon else "yfinance"
