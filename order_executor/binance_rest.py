"""
Binance REST client — Phase 2 scaffold (testnet/mainnet via BINANCE_API_BASE_URL).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("BinanceREST")

D = Decimal


class BinanceAPIError(Exception):
    def __init__(self, code: int, message: str, payload: Any = None):
        super().__init__(f"Binance API {code}: {message}")
        self.code = code
        self.message = message
        self.payload = payload


class BinanceRestClient:
    """Signed REST calls for spot orders (Phase 2 — not wired to production yet)."""

    def __init__(self) -> None:
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.base_url = os.getenv(
            "BINANCE_API_BASE_URL",
            "https://testnet.binance.vision",
        ).rstrip("/")
        self.recv_window = int(os.getenv("BINANCE_RECV_WINDOW_MS", "5000"))

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> Any:
        if signed and (not self.api_key or not self.api_secret):
            raise BinanceAPIError(-1, "BINANCE_API_KEY/SECRET required for signed endpoints")

        params = dict(params or {})
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            query = urlencode(params)
            params["signature"] = self._sign(query)

        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "GET":
                resp = await client.get(url, params=params, headers=headers)
            elif method == "POST":
                resp = await client.post(url, params=params, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(url, params=params, headers=headers)
            else:
                raise ValueError(method)

        data = resp.json()
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("code", 0) < 0):
            code = data.get("code", resp.status_code) if isinstance(data, dict) else resp.status_code
            msg = data.get("msg", resp.text) if isinstance(data, dict) else resp.text
            raise BinanceAPIError(int(code), str(msg), data)
        return data

    async def exchange_info(self, symbol: str | None = None) -> dict:
        params = {"symbol": symbol.upper()} if symbol else {}
        return await self._request("GET", "/api/v3/exchangeInfo", params)

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: D,
    ) -> dict:
        """Phase 2 — raises until LiveEngine gate is passed."""
        raise NotImplementedError(
            "Live market orders disabled until Phase 2 testnet gate. "
            "Set PAPER_TRADING=true or complete Fase 2 checklist."
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: D,
        price: D,
        time_in_force: str = "GTC",
    ) -> dict:
        raise NotImplementedError("Live limit orders — Phase 2 only")

    async def cancel_all_open_orders(self, symbol: str) -> list:
        return await self._request(
            "DELETE",
            "/api/v3/openOrders",
            {"symbol": symbol.upper()},
            signed=True,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list:
        params = {"symbol": symbol.upper()} if symbol else {}
        return await self._request("GET", "/api/v3/openOrders", params, signed=True)

    async def get_account(self) -> dict:
        return await self._request("GET", "/api/v3/account", signed=True)
