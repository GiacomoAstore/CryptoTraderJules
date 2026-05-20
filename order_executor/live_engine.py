"""
Live execution engine — Phase 2 scaffold.

Gate: Fase 1 paper metrics + Fase 2 testnet reconciliation before enabling.
"""
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from binance_rest import BinanceRestClient
from exchange_rules import SymbolFilters, parse_symbol_filters

if TYPE_CHECKING:
    from main import OrderCommand

logger = logging.getLogger("LiveEngine")

D = Decimal


class LiveEngine:
    """
    Placeholder for real Binance execution.
    Methods are structured for Phase 2; production calls remain blocked.
    """

    def __init__(self, redis_client, db_pool) -> None:
        self.redis_client = redis_client
        self.db_pool = db_pool
        self.rest = BinanceRestClient()
        self._filters: dict[str, SymbolFilters] = {}
        self.open_positions: dict[str, Any] = {}
        self._reconcile_interval = int(os.getenv("LIVE_RECONCILE_INTERVAL_SEC", "300"))

    async def bootstrap(self) -> None:
        logger.warning(
            "LiveEngine scaffold loaded — orders NOT sent until Phase 2 gate. "
            "Use testnet + PAPER_TRADING=false only after checklist."
        )
        await self.load_exchange_rules("BTCUSDT")
        await self.reconcile_positions()

    async def load_exchange_rules(self, symbol: str) -> SymbolFilters:
        info = await self.rest.exchange_info(symbol)
        rules = parse_symbol_filters(info, symbol)
        self._filters[symbol.upper()] = rules
        return rules

    async def reconcile_positions(self) -> dict:
        """
        Bot state ↔ exchange (Phase 2).
        Returns discrepancy report; scaffold returns empty OK.
        """
        logger.info("reconcile_positions: scaffold — implement in Phase 2")
        return {"status": "scaffold", "discrepancies": []}

    async def kill_switch(self, symbol: str | None = None) -> None:
        """Cancel all open orders + flatten (Phase 2)."""
        logger.critical("KILL SWITCH invoked (scaffold)")
        if symbol:
            await self.rest.cancel_all_open_orders(symbol)
        raise NotImplementedError("kill_switch market close — Phase 2")

    async def process_new_command(self, cmd_data: dict) -> None:
        from main import is_bot_running

        if not await is_bot_running(self.redis_client):
            logger.info("Live order ignored: bot not running")
            return

        cmd = cmd_data
        symbol = cmd.get("symbol", "").upper()
        rules = self._filters.get(symbol) or await self.load_exchange_rules(symbol)
        qty = rules.round_qty(D(str(cmd.get("quantity", "0"))))
        price = D(str(cmd.get("target_price", "0")))
        ok, reason = rules.validate_order(cmd.get("type", "BUY"), qty, price)
        if not ok:
            logger.error("Order rejected by exchange rules: %s", reason)
            return

        logger.error(
            "LiveEngine blocked order (Phase 2): %s %s %s @ %s",
            cmd.get("type"),
            qty,
            symbol,
            price,
        )
        await self.redis_client.publish(
            "system:alerts",
            json.dumps({
                "level": "error",
                "message": "Live trading not enabled — Phase 2 gate required",
                "symbol": symbol,
            }),
        )

    async def monitor_ticks(self, tick: dict) -> None:
        """SL/TP/trailing on live positions — Phase 2."""
        pass
