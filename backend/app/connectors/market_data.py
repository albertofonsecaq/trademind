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

from app.core.config import settings

log = logging.getLogger(__name__)

# Rate-limit guard: wait between consecutive API calls (yfinance can throttle)
_CALL_INTERVAL_S = 0.5


def _fetch_yfinance(symbol: str, start: date, end: date) -> list[dict]:
    """Synchronous yfinance fetch, run in executor."""
    try:
        import yfinance as yf
        time.sleep(_CALL_INTERVAL_S)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # end is exclusive in yfinance
            auto_adjust=True,
        )
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
        return bars
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", symbol, e)
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

        loop = asyncio.get_event_loop()
        if self._use_polygon:
            bars = await loop.run_in_executor(None, _fetch_polygon, symbol, start, end)
        else:
            bars = await loop.run_in_executor(None, _fetch_yfinance, symbol, start, end)

        # Cap to the requested window
        cutoff = start + timedelta(days=(days or settings.VALIDATION_WINDOW_DAYS))
        return [b for b in bars if b["date"] <= cutoff]

    def data_source_name(self) -> str:
        return "polygon" if self._use_polygon else "yfinance"
