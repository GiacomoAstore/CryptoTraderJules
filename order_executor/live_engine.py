"""
Live execution engine — Phase 2 scaffold.

Gate: Fase 1 paper metrics + Fase 2 testnet reconciliation before enabling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from cryptocom_rest import CryptocomRestClient
from exchange_rules import SymbolFilters, parse_symbol_filters

if TYPE_CHECKING:
    from main import OrderCommand

logger = logging.getLogger("LiveEngine")

D = Decimal


class LiveEngine:
    """
    Placeholder for real Crypto.com execution.
    Methods are structured for Phase 2; production calls remain blocked.
    """

    def __init__(self, redis_client, db_pool) -> None:
        self.redis_client = redis_client
        self.db_pool = db_pool
        self.rest = CryptocomRestClient()
        self._filters: dict[str, SymbolFilters] = {}
        self.open_positions: dict[str, Any] = {}
        self._reconcile_interval = int(os.getenv("LIVE_RECONCILE_INTERVAL_SEC", "300"))

    def __init__(self, redis_client, db_pool) -> None:
        self.redis_client = redis_client
        self.db_pool = db_pool
        self.rest = CryptocomRestClient()
        self._filters: dict[str, SymbolFilters] = {}
        self.open_positions: dict[str, Any] = {}
        self._reconcile_interval = int(os.getenv("LIVE_RECONCILE_INTERVAL_SEC", "15"))
        self._reconcile_task: asyncio.Task | None = None
        self._state_lock = asyncio.Lock()

    async def bootstrap(self) -> None:
        logger.warning(
            "LiveEngine loaded — orders NOT sent until Phase 2 gate. "
            "Reconciliation loop active every %ds.",
            self._reconcile_interval,
        )
        await self.load_exchange_rules("BTC_USDT")
        await self.reconcile_positions()
        self.start_reconcile_loop()

    def start_reconcile_loop(self) -> None:
        """Start the background periodic reconciliation loop."""
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())
            logger.info("Background position reconciliation loop started (interval: %ds)", self._reconcile_interval)

    async def sync_account_balances(self) -> None:
        """Fetch live USDT balance from exchange and sync to Redis."""
        try:
            summary = await self.rest.get_account_summary()
            data = summary.get("result", {}).get("accounts", [])
            for acc in data:
                if acc.get("currency", "").upper() == "USDT":
                    total_bal = str(acc.get("balance", "0"))
                    free_bal = str(acc.get("available", "0"))
                    if self.redis_client:
                        await self.redis_client.set("live:balance:usdt", total_bal, ex=30)
                        await self.redis_client.set("live:balance:free_usdt", free_bal, ex=30)
                    logger.debug("Synced live USDT balance: total=%s, free=%s", total_bal, free_bal)
                    break
        except Exception as exc:
            logger.warning("Failed to sync live account balances: %s", exc)

    async def _reconcile_loop(self) -> None:
        """Periodic background reconciliation task."""
        while True:
            try:
                await asyncio.sleep(self._reconcile_interval)
                await self.sync_account_balances()
                await self.reconcile_positions()
            except asyncio.CancelledError:
                logger.info("Reconciliation loop cancelled")
                break
            except Exception as exc:
                logger.error("Error in reconciliation loop: %s", exc)

    async def load_exchange_rules(self, symbol: str) -> SymbolFilters:
        info = await self.rest.exchange_info(symbol)
        rules = parse_symbol_filters(info, symbol)
        self._filters[symbol.upper()] = rules
        return rules

    async def close_reconciled_position(
        self, pos_key: str, pos: Any, close_price: Decimal, reason: str
    ) -> dict[str, Any]:
        """Helper to close position in-memory, update DB, and emit Redis events during reconciliation."""
        COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))

        direction = getattr(pos, "direction", getattr(pos, "side", "BUY"))
        exec_price = Decimal(str(getattr(pos, "executed_price", getattr(pos, "entry_price", "0"))))
        qty = Decimal(str(getattr(pos, "quantity", "0")))
        symbol = getattr(pos, "symbol", "")
        ab_variant = getattr(pos, "ab_variant", "A")

        if direction == "BUY":
            pnl_usdt = (close_price - exec_price) * qty
        else:
            pnl_usdt = (exec_price - close_price) * qty

        fee = (exec_price * qty * COMMISSION_RATE) + (close_price * qty * COMMISSION_RATE)
        net_pnl = pnl_usdt - fee
        pnl_pct = (net_pnl / (exec_price * qty)) * 100 if exec_price * qty > 0 else Decimal("0")

        # Update DB if pool is available
        if self.db_pool:
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM positions WHERE symbol = $1 AND ab_variant = $2",
                        symbol,
                        ab_variant,
                    )
            except Exception as e:
                logger.error("Error deleting reconciled position from DB: %s", e)

        trade_record = {
            "symbol": symbol,
            "side": direction,
            "price": round(float(close_price), 4),
            "entry_price": round(float(exec_price), 4),
            "exit_price": round(float(close_price), 4),
            "quantity": round(float(qty), 6),
            "pnl_usdt": round(float(net_pnl), 2),
            "pnl_pct": round(float(pnl_pct), 2),
            "close_reason": reason,
            "ab_variant": ab_variant,
            "strategy_name": getattr(pos, "strategy", "Unknown"),
            "open_time_ts": getattr(pos, "created_at", 0),
        }

        # Publish Redis events if client is available
        if self.redis_client:
            try:
                await self.redis_client.publish("executed_trades", json.dumps(trade_record))
                event_payload = {
                    "status": "FILLED",
                    "pos_key": pos_key,
                    "command_id": getattr(pos, "command_id", pos_key),
                    "symbol": symbol,
                    "ab_variant": ab_variant,
                    "side": direction,
                    "quantity": float(qty),
                    "strategy_name": getattr(pos, "strategy", "Unknown"),
                    "close_reason": reason,
                }
                event_payload.update(trade_record)
                await self.redis_client.publish("order_events", json.dumps(event_payload))
            except Exception as exc:
                logger.error("Error publishing reconciled events to Redis: %s", exc)

        if pos_key in self.open_positions:
            del self.open_positions[pos_key]
        if self.redis_client:
            try:
                await self.redis_client.delete(f"positions:active:{pos_key}")
            except Exception:
                pass

        logger.info(
            "RECONCILED POSITION CLOSED [%s]: %s @ %s | Reason: %s | PNL: %.2f (%.2f%%)",
            ab_variant,
            symbol,
            close_price,
            reason,
            float(net_pnl),
            float(pnl_pct),
        )
        return trade_record

    async def _cancel_orphan_order_with_retry(
        self, symbol: str, orphan_order_id: str, role: str, pos_key: str
    ) -> bool:
        """Retry cancel_order up to 2 attempts and emit critical alert if cleanup fails."""
        for attempt in range(1, 3):
            try:
                logger.info(
                    "Cancelling orphan %s order %s on exchange (attempt %d/2)",
                    role,
                    orphan_order_id,
                    attempt,
                )
                await self.rest.cancel_order(symbol, str(orphan_order_id))
                return True
            except Exception as exc:
                logger.warning(
                    "Failed to cancel orphan %s order %s (attempt %d/2): %s",
                    role,
                    orphan_order_id,
                    attempt,
                    exc,
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)

        # Cleanup failed after retries — emit critical alert
        logger.critical(
            "ORPHAN ORDER CLEANUP FAILED: %s order %s for %s remains open on exchange!",
            role,
            orphan_order_id,
            pos_key,
        )
        if self.redis_client:
            alert_payload = json.dumps({
                "level": "critical",
                "event": "ORPHAN_ORDER_CLEANUP_FAILED",
                "message": f"ORPHAN ORDER CLEANUP FAILED: {role} order {orphan_order_id} remains open on exchange!",
                "pos_key": pos_key,
                "symbol": symbol,
                "orphan_order_id": str(orphan_order_id),
                "role": role,
            })
            await self.redis_client.publish("system:alerts", alert_payload)
        return False

    async def _replace_resized_order_with_retry(
        self, symbol: str, side: str, remaining_qty: Decimal, stop_price: Decimal, orphan_role: str, pos_key: str
    ) -> str | None:
        """Retry re-placing a resized conditional order up to 2 attempts, emitting POSITION_UNPROTECTED_AFTER_RESIZE if it fails."""
        for attempt in range(1, 3):
            try:
                logger.info(
                    "Re-placing resized %s order for %s (qty %s, trigger %s) attempt %d/2",
                    orphan_role,
                    pos_key,
                    remaining_qty,
                    stop_price,
                    attempt,
                )
                resp = await self.rest.create_stop_loss_order(
                    instrument=symbol,
                    side=side,
                    quantity=remaining_qty,
                    stop_price=stop_price,
                )
                new_id = str(resp.get("order_id"))
                logger.info("Resized %s order re-placed successfully: %s", orphan_role, new_id)
                return new_id
            except Exception as exc:
                logger.warning(
                    "Failed to re-place resized %s order (attempt %d/2): %s",
                    orphan_role,
                    attempt,
                    exc,
                )
                if attempt < 2:
                    await asyncio.sleep(0.5)

        # Re-placement failed after retries — emit critical alert
        logger.critical(
            "POSITION UNPROTECTED AFTER RESIZE: Failed to re-place %s order for %s (remaining qty %s)!",
            orphan_role,
            pos_key,
            remaining_qty,
        )
        if self.redis_client:
            alert_payload = json.dumps({
                "level": "critical",
                "event": "POSITION_UNPROTECTED_AFTER_RESIZE",
                "message": f"POSITION UNPROTECTED AFTER RESIZE: Failed to re-place {orphan_role} order for {pos_key} (remaining qty {remaining_qty})!",
                "pos_key": pos_key,
                "symbol": symbol,
                "remaining_qty": float(remaining_qty),
                "role": orphan_role,
            })
            await self.redis_client.publish("system:alerts", alert_payload)
        return None

    async def reconcile_positions(self) -> dict:
        """
        Active position reconciliation (Phase 2 Level 2).
        Protected by asyncio.Lock against concurrent mutations.
        Polls exchange open orders and syncs in-memory bot state for SL/TP orders (FILLED, PARTIALLY_FILLED, CANCELED).
        Cancels orphan conditional orders with retry + alert fallback.
        """
        async with self._state_lock:
            logger.info("Running reconcile_positions check across %d tracked positions", len(self.open_positions))
            discrepancies: list[dict[str, Any]] = []

            tracked_keys = list(self.open_positions.keys())

            for pos_key in tracked_keys:
                if pos_key not in self.open_positions:
                    continue

                pos = self.open_positions[pos_key]
                sl_order_id = getattr(pos, "sl_order_id", None)
                tp_order_id = getattr(pos, "tp_order_id", None)
                symbol = getattr(pos, "symbol", "")

                if not sl_order_id and not tp_order_id:
                    continue

                # Fetch open orders for instrument
                try:
                    open_orders = await self.rest.get_open_orders(symbol)
                except Exception as exc:
                    logger.error("Failed to fetch open orders for %s during reconcile: %s", symbol, exc)
                    continue

                open_order_ids = {str(ord_info.get("order_id")) for ord_info in open_orders}

                # Check SL and TP status against open orders
                for role, order_id in (("SL", sl_order_id), ("TP", tp_order_id)):
                    if not order_id or str(order_id) in open_order_ids:
                        continue  # Still open on exchange, no state change for this order

                    # Order is missing from open orders — query order detail to determine outcome
                    try:
                        order_detail = await self.rest.get_order_detail(str(order_id))
                    except Exception as exc:
                        logger.error("Failed to fetch order detail for %s: %s", order_id, exc)
                        continue

                    order_data = order_detail.get("order_info", order_detail) if isinstance(order_detail, dict) else {}
                    order_status = str(order_data.get("status", "")).upper()

                    if order_status in ("FILLED", "EXECUTED"):
                        avg_price = Decimal(str(order_data.get("avg_price", order_data.get("price", "0"))))
                        if avg_price <= 0:
                            default_fallback = getattr(pos, "take_profit" if role == "TP" else "stop_loss", getattr(pos, "executed_price", "0"))
                            avg_price = Decimal(str(default_fallback))

                        reason = "EXCHANGE_TP_HIT" if role == "TP" else "EXCHANGE_SL_HIT"
                        trade_rec = await self.close_reconciled_position(pos_key, pos, avg_price, reason)

                        # Clean up orphan order on exchange with retry + alert
                        orphan_order_id = tp_order_id if role == "SL" else sl_order_id
                        orphan_role = "TP" if role == "SL" else "SL"
                        orphan_cancelled = False
                        if orphan_order_id and str(orphan_order_id) in open_order_ids:
                            orphan_cancelled = await self._cancel_orphan_order_with_retry(
                                symbol, str(orphan_order_id), orphan_role, pos_key
                            )

                        discrepancies.append({
                            "pos_key": pos_key,
                            "type": f"{role}_FILLED_ON_EXCHANGE",
                            "order_id": order_id,
                            "orphan_cancelled": orphan_cancelled,
                            "close_price": float(avg_price),
                            "trade_record": trade_rec,
                        })
                        break  # Position closed, break role loop

                    elif order_status in ("PARTIALLY_FILLED", "PARTIAL_FILLED"):
                        filled_qty = Decimal(str(order_data.get("cumulative_quantity", order_data.get("quantity", "0"))))
                        avg_price = Decimal(str(order_data.get("avg_price", order_data.get("price", "0"))))
                        current_qty = Decimal(str(getattr(pos, "quantity", "0")))

                        # Adjust remaining position quantity
                        remaining_qty = max(Decimal("0"), current_qty - filled_qty)
                        setattr(pos, "quantity", remaining_qty)

                        # Resize opposite conditional order to match remaining_qty
                        orphan_order_id = tp_order_id if role == "SL" else sl_order_id
                        orphan_role = "TP" if role == "SL" else "SL"
                        new_orphan_order_id = None

                        if orphan_order_id and str(orphan_order_id) in open_order_ids and remaining_qty > 0:
                            logger.info(
                                "Resizing opposite %s order for %s: cancelling old order %s (qty %s) and replacing for remaining qty %s",
                                orphan_role,
                                pos_key,
                                orphan_order_id,
                                current_qty,
                                remaining_qty,
                            )
                            # Cancel old oversized order
                            await self._cancel_orphan_order_with_retry(
                                symbol, str(orphan_order_id), orphan_role, pos_key
                            )
                            # Re-place opposite order for remaining_qty with retry and fallback alert
                            opp_side = "SELL" if getattr(pos, "direction", getattr(pos, "side", "BUY")) == "BUY" else "BUY"
                            opp_trigger = getattr(pos, "take_profit" if orphan_role == "TP" else "stop_loss", Decimal("0"))
                            if opp_trigger > 0:
                                new_orphan_order_id = await self._replace_resized_order_with_retry(
                                    symbol, opp_side, remaining_qty, opp_trigger, orphan_role, pos_key
                                )
                                if orphan_role == "TP":
                                    setattr(pos, "tp_order_id", new_orphan_order_id)
                                else:
                                    setattr(pos, "sl_order_id", new_orphan_order_id)

                        logger.warning(
                            "PARTIAL FILL RECONCILED [%s]: %s %s filled %s @ %s | Remaining qty: %s | Resized opposite %s order_id: %s",
                            pos_key,
                            role,
                            symbol,
                            filled_qty,
                            avg_price,
                            remaining_qty,
                            orphan_role,
                            new_orphan_order_id,
                        )

                        # Update DB with remaining quantity if DB pool available
                        if self.db_pool and remaining_qty > 0:
                            try:
                                async with self.db_pool.acquire() as conn:
                                    await conn.execute(
                                        "UPDATE positions SET quantity = $1 WHERE symbol = $2 AND ab_variant = $3",
                                        remaining_qty,
                                        getattr(pos, "symbol", ""),
                                        getattr(pos, "ab_variant", "A"),
                                    )
                            except Exception as e:
                                logger.error("DB Error updating partial fill quantity: %s", e)

                        discrepancies.append({
                            "pos_key": pos_key,
                            "type": f"{role}_PARTIALLY_FILLED_ON_EXCHANGE",
                            "order_id": order_id,
                            "filled_qty": float(filled_qty),
                            "remaining_qty": float(remaining_qty),
                            "avg_price": float(avg_price),
                            "resized_opposite_order_id": new_orphan_order_id,
                        })

                    elif order_status in ("CANCELED", "EXPIRED", "REJECTED"):
                        logger.critical(
                            "DISCREPANCY DETECTED: Native %s order %s for %s was %s without position close!",
                            role,
                            order_id,
                            pos_key,
                            order_status,
                        )
                        if self.redis_client:
                            alert_payload = json.dumps({
                                "level": "critical",
                                "event": "UNPROTECTED_POSITION",
                                "message": f"UNPROTECTED POSITION: Native {role} order {order_id} was {order_status} on exchange!",
                                "pos_key": pos_key,
                                "symbol": symbol,
                                "order_id": order_id,
                                "status": order_status,
                            })
                            await self.redis_client.publish("system:alerts", alert_payload)

                        discrepancies.append({
                            "pos_key": pos_key,
                            "type": f"{role}_ORDER_UNEXPECTEDLY_CANCELED",
                            "order_id": order_id,
                            "order_status": order_status,
                        })

            return {
                "status": "ok",
                "checked_positions": len(tracked_keys),
                "discrepancies_found": len(discrepancies),
                "discrepancies": discrepancies,
            }

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

        async with self._state_lock:
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
