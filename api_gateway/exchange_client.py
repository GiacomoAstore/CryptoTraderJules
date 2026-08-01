"""
Crypto.com Exchange client for API Gateway — portfolio & account queries.

Replaces the old BinanceClient used by api_gateway/main.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import sys

sys.path.insert(0, "/app/shared_config")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared_config")))

try:
    from cryptocom_auth import generate_cryptocom_signature, params_to_str
except ImportError:
    from shared_config.cryptocom_auth import generate_cryptocom_signature, params_to_str

logger = logging.getLogger("ExchangeClient")


class ExchangeClient:
    """Crypto.com Exchange REST client for account/portfolio queries."""

    def __init__(self) -> None:
        self.api_key = os.getenv("CRYPTOCOM_API_KEY", "")
        self.api_secret = os.getenv("CRYPTOCOM_API_SECRET", "")
        self.base_url = os.getenv(
            "CRYPTOCOM_API_BASE_URL",
            "https://api.crypto.com/exchange/v1",
        ).rstrip("/")
        self._req_id = 0

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

    async def _private_request(self, method: str, params: dict | None = None) -> Any:
        if not self.api_key or not self.api_secret:
            raise ValueError("CRYPTOCOM_API_KEY/SECRET required")

        req_id = self._next_id()
        nonce = int(time.time() * 1000)
        params = dict(params or {})
        sig = self._sign(method, req_id, params, nonce)

        body = {
            "id": req_id,
            "method": method,
            "params": params,
            "sig": sig,
            "api_key": self.api_key,
            "nonce": nonce,
        }
        url = f"{self.base_url}/{method}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)

        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

        data = resp.json()
        code = data.get("code", -1)
        if code != 0:
            msg = data.get("msg", data.get("message", "unknown error"))
            return {"error": f"Crypto.com API {code}: {msg}"}
        return data.get("result", data)

    async def get_real_portfolio(self) -> list | dict:
        """Fetch real account balances from Crypto.com Exchange.

        Returns a list of dicts compatible with the format the API Gateway expects:
          [{"asset": "BTC", "free": "0.5", "locked": "0.1"}, ...]
        or {"error": "..."} on failure.
        """
        if not self.api_key or not self.api_secret:
            return {"error": "Crypto.com API keys not configured"}

        result = await self._private_request("private/get-account-summary")

        if isinstance(result, dict) and "error" in result:
            return result

        # Crypto.com returns: result.accounts[]
        # Each account has: currency, balance, available, order, stake, ...
        accounts = result.get("accounts", [])
        balances = []
        for acc in accounts:
            available = float(acc.get("available", 0))
            order_hold = float(acc.get("order", 0))
            if available > 0 or order_hold > 0:
                balances.append({
                    "asset": acc.get("currency", ""),
                    "free": str(available),
                    "locked": str(order_hold),
                })
        return balances
