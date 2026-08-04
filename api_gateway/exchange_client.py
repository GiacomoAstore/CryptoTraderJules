"""
Crypto.com Exchange client for API Gateway — portfolio & account queries.

Replaces the old BinanceClient used by api_gateway/main.py.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from typing import Any

import httpx

sys.path.insert(0, "/app/shared_config")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shared_config")))

try:
    from cryptocom_auth import generate_cryptocom_signature, params_to_str
except ImportError:
    try:
        from shared_config.cryptocom_auth import generate_cryptocom_signature, params_to_str
    except ImportError:
        def params_to_str(params: dict[str, Any] | None) -> str:
            if not params:
                return ""
            return "".join([f"{k}{params[k]}" for k in sorted(params.keys())])

        def generate_cryptocom_signature(
            method: str,
            req_id: int | str,
            api_key: str,
            api_secret: str,
            params: dict[str, Any] | None,
            nonce: int | str,
        ) -> str:
            p_str = params_to_str(params)
            payload = f"{method}{req_id}{api_key}{p_str}{nonce}"
            return hmac.new(
                api_secret.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

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

        result = await self._private_request("private/user-balance")

        if isinstance(result, dict) and "error" in result:
            return result

        balances = []
        # Crypto.com v1 returns data list or accounts list
        data_items = result.get("data", []) if isinstance(result, dict) else []
        accounts = result.get("accounts", []) if isinstance(result, dict) else []
        
        # 1. Parse accounts array if present
        for acc in accounts:
            available = float(acc.get("available", 0))
            order_hold = float(acc.get("order", 0))
            currency = acc.get("currency", "")
            if currency and (available > 0 or order_hold > 0):
                balances.append({
                    "asset": currency,
                    "free": str(available),
                    "locked": str(order_hold),
                })

        # 2. Parse data items if present
        for item in data_items:
            # Handle position_balances array inside user-balance item
            pos_balances = item.get("position_balances", [])
            for pb in pos_balances:
                curr = pb.get("currency", pb.get("instrument_name", ""))
                avail = float(pb.get("quantity", pb.get("available", 0)))
                locked = float(pb.get("reserved", pb.get("locked", 0)))
                if curr and (avail > 0 or locked > 0):
                    balances.append({
                        "asset": curr,
                        "free": str(avail),
                        "locked": str(locked),
                    })
            # Handle main item cash balance
            main_curr = item.get("instrument_name", "")
            main_cash = float(item.get("total_cash_balance", item.get("total_available_balance", 0)))
            if main_curr and main_cash > 0 and not any(b["asset"] == main_curr for b in balances):
                balances.append({
                    "asset": main_curr,
                    "free": str(main_cash),
                    "locked": "0.0",
                })

        return balances
