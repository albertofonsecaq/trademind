"""
Alpaca paper-trading connector.
Wraps the Alpaca REST API v2 (paper endpoint) for order submission and status polling.
Only paper mode is used in Phase 9; live mode is a deliberate later opt-in.
"""
from decimal import Decimal
import httpx
from app.core.config import settings


class AlpacaError(Exception):
    pass


class AlpacaConnector:
    def __init__(self):
        self._base = settings.ALPACA_PAPER_BASE_URL.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
            "Content-Type": "application/json",
        }

    def _check_configured(self):
        if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
            raise AlpacaError(
                "Alpaca paper trading is not configured. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your server environment."
            )

    async def submit_order(
        self,
        symbol: str,
        action: str,           # "long" | "short"
        size: Decimal,         # number of shares
        entry: Decimal | None, # limit price; None = market order
    ) -> dict:
        """
        Place a paper order on Alpaca. Returns the full Alpaca order object.
        action="long" → side="buy"; action="short" → side="sell".
        entry set → limit order; entry None → market order.
        """
        self._check_configured()
        side = "buy" if action == "long" else "sell"
        order_type = "limit" if entry is not None else "market"
        payload: dict = {
            "symbol": symbol.upper(),
            "qty": str(size),
            "side": side,
            "type": order_type,
            "time_in_force": "gtc",
        }
        if entry is not None:
            payload["limit_price"] = str(entry)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/v2/orders",
                json=payload,
                headers=self._headers,
                timeout=15,
            )
        if resp.status_code not in (200, 201):
            raise AlpacaError(f"Alpaca order submission failed ({resp.status_code}): {resp.text}")
        return resp.json()

    async def get_order(self, alpaca_order_id: str) -> dict:
        """Fetch current status of an Alpaca order."""
        self._check_configured()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/v2/orders/{alpaca_order_id}",
                headers=self._headers,
                timeout=15,
            )
        if resp.status_code == 404:
            raise AlpacaError(f"Order {alpaca_order_id} not found on Alpaca")
        if resp.status_code != 200:
            raise AlpacaError(f"Alpaca status check failed ({resp.status_code}): {resp.text}")
        return resp.json()

    async def cancel_order(self, alpaca_order_id: str) -> None:
        """Cancel a pending Alpaca order."""
        self._check_configured()
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self._base}/v2/orders/{alpaca_order_id}",
                headers=self._headers,
                timeout=15,
            )
        # 204 = cancelled; 422 = already in terminal state — both are acceptable here
        if resp.status_code not in (204, 422):
            raise AlpacaError(f"Alpaca cancel failed ({resp.status_code}): {resp.text}")


# Alpaca status → our internal status mapping
ALPACA_TERMINAL_MAP: dict[str, str] = {
    "filled": "filled",
    "partially_filled": "submitted",   # still in flight
    "canceled": "cancelled",
    "expired": "cancelled",
    "rejected": "rejected",
    "done_for_day": "cancelled",
    "replaced": "cancelled",
    "pending_cancel": "submitted",     # still in flight
    "pending_replace": "submitted",
    "new": "submitted",
    "held": "submitted",
    "accepted": "submitted",
    "pending_new": "submitted",
    "accepted_for_bidding": "submitted",
    "stopped": "submitted",
    "suspended": "submitted",
    "calculated": "submitted",
}
