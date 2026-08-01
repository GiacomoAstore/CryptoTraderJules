"""
Crypto.com Exchange REST client — v1 API.

Handles authentication (HMAC-SHA256), request signing, and all
private/public REST endpoints for spot trading.

Env vars:
  CRYPTOCOM_API_KEY, CRYPTOCOM_API_SECRET, CRYPTOCOM_API_BASE_URL
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

import httpx
import sys

sys.path.insert(0, "/app/shared_config")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared_config")))

try:
    from cryptocom_auth import generate_cryptocom_signature, params_to_str
except ImportError:
    from shared_config.cryptocom_auth import generate_cryptocom_signature, params_to_str

try:
    from exchange_adapter import ExchangeAdapter
except ImportError:
    from order_executor.exchange_adapter import ExchangeAdapter

logger = logging.getLogger("CryptocomREST")

D = Decimal

# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------

def binance_to_cryptocom_symbol(symbol: str) -> str:
    """Convert Binance-style 'BTCUSDT' to Crypto.com-style 'BTC_USDT'.

    Handles common quote currencies: USDT, USDC, USD, BTC, ETH, CRO.
    """
    symbol = symbol.upper()
    if "_" in symbol:
        return symbol  # Already in Crypto.com format

    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "CRO"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}_{quote}"
    # Fallback: assume last 4 chars are quote (USDT is most common)
    return f"{symbol[:-4]}_{symbol[-4:]}" if len(symbol) > 4 else symbol


def cryptocom_to_binance_symbol(symbol: str) -> str:
    """Convert Crypto.com-style 'BTC_USDT' to Binance-style 'BTCUSDT'."""
    return symbol.replace("_", "")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CryptocomAPIError(Exception):
    """Raised for non-zero response codes from the Crypto.com API."""

    def __init__(self, code: int, message: str, payload: Any = None):
        super().__init__(f"Crypto.com API {code}: {message}")
        self.code = code
        self.message = message
        self.payload = payload


# ---------------------------------------------------------------------------
# REST Client
# ---------------------------------------------------------------------------

class CryptocomRestClient(ExchangeAdapter):
    """Crypto.com Exchange v1 REST client with HMAC-SHA256 signing."""

    def __init__(self) -> None:
        self.api_key = os.getenv("CRYPTOCOM_API_KEY", "")
        self.api_secret = os.getenv("CRYPTOCOM_API_SECRET", "")
        self.base_url = os.getenv(
            "CRYPTOCOM_API_BASE_URL",
            "https://api.crypto.com/exchange/v1",
        ).rstrip("/")
        self._req_id = 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    @staticmethod
    def _params_to_str(params: dict) -> str:
        return params_to_str(params)

    def _sign(self, method: str, req_id: int, params: dict, nonce: int) -> str:
        return generate_cryptocom_signature(
            method=method,
            req_id=req_id,
            api_key=self.api_key,
            api_secret=self.api_secret,
            params=params,
            nonce=nonce,
        )

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    async def _public_request(self, method: str, params: dict | None = None) -> Any:
        """Send an unauthenticated GET request to a public endpoint with exponential backoff retry."""
        url = f"{self.base_url}/{method}"
        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, params=params or {})

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    logger.warning(
                        "Rate limit hit (HTTP 429) on %s (attempt %d/%d) — sleeping %.2fs",
                        method,
                        attempt,
                        max_retries,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    backoff *= 2
                    continue

                return self._handle_response(resp)

            except (httpx.TimeoutException, httpx.NetworkError) as err:
                if attempt == max_retries:
                    logger.error("Public request %s failed after %d attempts: %s", method, max_retries, err)
                    raise
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s — retrying in %.1fs",
                    method,
                    attempt,
                    max_retries,
                    err,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2

    async def _private_request(self, method: str, params: dict | None = None) -> Any:
        """Send a signed (authenticated) request to a private endpoint with exponential backoff retry."""
        if not self.api_key or not self.api_secret:
            raise CryptocomAPIError(
                -1, "CRYPTOCOM_API_KEY/SECRET required for private endpoints"
            )

        max_retries = 3
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            req_id = self._next_id()
            nonce = int(time.time() * 1000)
            params_dict = dict(params or {})
            sig = self._sign(method, req_id, params_dict, nonce)

            body = {
                "id": req_id,
                "method": method,
                "params": params_dict,
                "sig": sig,
                "api_key": self.api_key,
                "nonce": nonce,
            }
            url = f"{self.base_url}/{method}"

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=body)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    logger.warning(
                        "Rate limit hit (HTTP 429) on %s (attempt %d/%d) — sleeping %.2fs",
                        method,
                        attempt,
                        max_retries,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    backoff *= 2
                    continue

                return self._handle_response(resp)

            except (httpx.TimeoutException, httpx.NetworkError) as err:
                if attempt == max_retries:
                    logger.error("Private request %s failed after %d attempts: %s", method, max_retries, err)
                    raise
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s — retrying in %.1fs",
                    method,
                    attempt,
                    max_retries,
                    err,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2

    @staticmethod
    def _handle_response(resp: httpx.Response) -> Any:
        """Parse response and raise on error."""
        try:
            data = resp.json()
        except Exception:
            raise CryptocomAPIError(resp.status_code, resp.text[:500])

        code = data.get("code", -1)
        if resp.status_code >= 400 or code != 0:
            msg = data.get("msg", data.get("message", resp.text[:500]))
            raise CryptocomAPIError(int(code), str(msg), data)
        return data.get("result", data)

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_instruments(self, instrument: str | None = None) -> dict:
        """Get instrument metadata (precision, min/max qty, etc.)."""
        params: dict[str, Any] = {}
        if instrument:
            params["instrument_name"] = instrument
        return await self._public_request("public/get-instruments", params)

    async def get_ticker(self, instrument: str) -> dict:
        """Get ticker with best bid/ask and last price."""
        return await self._public_request(
            "public/get-ticker", {"instrument_name": instrument}
        )

    async def get_book(self, instrument: str, depth: int = 20) -> dict:
        """Get order book snapshot."""
        return await self._public_request(
            "public/get-book",
            {"instrument_name": instrument, "depth": depth},
        )

    async def get_candlestick(
        self, instrument: str, timeframe: str = "1m", count: int = 300
    ) -> list[dict]:
        """Get OHLCV candlestick data.

        timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 6h, 12h, 1D, 7D, 14D, 1M
        """
        result = await self._public_request(
            "public/get-candlestick",
            {"instrument_name": instrument, "timeframe": timeframe, "count": count},
        )
        # Normalise to list of dicts matching internal format
        bars = []
        for row in result.get("data", []):
            bars.append({
                "open": D(str(row["o"])),
                "high": D(str(row["h"])),
                "low": D(str(row["l"])),
                "close": D(str(row["c"])),
                "volume": D(str(row["v"])),
                "ts": int(row["t"]),
            })
        return bars

    # ------------------------------------------------------------------
    # Private endpoints — account
    # ------------------------------------------------------------------

    async def get_account_summary(self) -> dict:
        """Get account balances (all currencies)."""
        return await self._private_request("private/get-account-summary")

    # ------------------------------------------------------------------
    # Private endpoints — orders
    # ------------------------------------------------------------------

    async def create_order(
        self,
        instrument: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        time_in_force: str | None = None,
    ) -> dict:
        """Place a new order.

        side: BUY or SELL
        order_type: LIMIT or MARKET
        time_in_force: GOOD_TILL_CANCEL, FILL_OR_KILL, IMMEDIATE_OR_CANCEL
        """
        params: dict[str, Any] = {
            "instrument_name": instrument,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if price is not None and order_type.upper() == "LIMIT":
            params["price"] = str(price)
        if time_in_force:
            params["time_in_force"] = time_in_force

        return await self._private_request("private/create-order", params)

    async def create_stop_loss_order(
        self,
        instrument: str,
        side: str,
        quantity: Decimal,
        stop_price: Decimal,
        limit_price: Decimal | None = None,
    ) -> dict:
        """Place a native conditional STOP_LOSS / STOP_LIMIT order on Crypto.com Exchange.

        side: BUY or SELL (opposite of open position)
        stop_price: Trigger price for the stop
        limit_price: Executed price for STOP_LIMIT order. If None, uses STOP_LOSS (market execution on trigger).
        """
        order_type = "STOP_LIMIT" if limit_price is not None else "STOP_LOSS"
        params: dict[str, Any] = {
            "instrument_name": instrument,
            "side": side.upper(),
            "type": order_type,
            "quantity": str(quantity),
            "trigger_price": str(stop_price),
        }
        if limit_price is not None:
            params["price"] = str(limit_price)

        logger.info(
            "Creating native exchange %s order: %s %s qty=%s trigger_price=%s",
            order_type,
            side.upper(),
            instrument,
            quantity,
            stop_price,
        )
        return await self._private_request("private/create-order", params)

    async def cancel_order(self, instrument: str, order_id: str) -> dict:
        """Cancel a specific order."""
        return await self._private_request(
            "private/cancel-order",
            {"instrument_name": instrument, "order_id": order_id},
        )

    async def cancel_stop_loss_order(self, instrument: str, order_id: str) -> dict:
        """Cancel a native conditional Stop Loss order when position is closed normally (e.g. TP hit)."""
        logger.info("Cancelling native Stop Loss order %s for %s", order_id, instrument)
        return await self.cancel_order(instrument, order_id)

    async def cancel_all_orders(self, instrument: str) -> list:
        """Cancel all open orders for an instrument."""
        result = await self._private_request(
            "private/cancel-all-orders",
            {"instrument_name": instrument},
        )
        return result if isinstance(result, list) else []

    async def get_open_orders(self, instrument: str | None = None) -> list:
        """List open orders."""
        params: dict[str, Any] = {}
        if instrument:
            params["instrument_name"] = instrument
        result = await self._private_request("private/get-open-orders", params)
        return result.get("data", []) if isinstance(result, dict) else result

    async def get_order_detail(self, order_id: str) -> dict:
        """Get details for a specific order."""
        return await self._private_request(
            "private/get-order-detail", {"order_id": order_id}
        )

    # ------------------------------------------------------------------
    # Convenience wrappers (match old BinanceRestClient interface)
    # ------------------------------------------------------------------

    async def exchange_info(self, symbol: str | None = None) -> dict:
        """Compatibility wrapper matching old BinanceRestClient.exchange_info()."""
        instrument = binance_to_cryptocom_symbol(symbol) if symbol else None
        return await self.get_instruments(instrument)

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: D,
    ) -> dict:
        """Phase 2 — raises until gate is passed."""
        raise NotImplementedError(
            "Live market orders disabled until Phase 2 testnet gate. "
            "Set PAPER_TRADING=true or complete Phase 2 checklist."
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: D,
        price: D,
        time_in_force: str = "GOOD_TILL_CANCEL",
    ) -> dict:
        raise NotImplementedError("Live limit orders — Phase 2 only")

    async def get_account(self) -> dict:
        """Compatibility wrapper: returns balances in Binance-like format."""
        result = await self.get_account_summary()
        accounts = result.get("accounts", [])
        # Normalise to [{"asset": ..., "free": ..., "locked": ...}]
        balances = []
        for acc in accounts:
            balances.append({
                "asset": acc.get("currency", ""),
                "free": str(acc.get("available", "0")),
                "locked": str(acc.get("order", "0")),
            })
        return {"balances": balances}
