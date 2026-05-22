import asyncio
import json
import logging
import os
import time
import uuid
import asyncpg
from typing import Dict, Optional
from collections import deque
from decimal import Decimal, getcontext
import redis.asyncio as redis

from risk_config import RiskParams, load_risk_params

getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskManager")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:"
    f"{os.getenv('DB_PASSWORD', 'crypto_pass')}@"
    f"{os.getenv('DB_HOST', 'timescaledb')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME', 'cryptoscalper_db')}"
)

# Symbols actively monitored — used by ATR updater loop
WATCHED_SYMBOLS = [s.upper().strip() for s in os.getenv(
    "WATCHED_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,SHIBUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,TRXUSDT,LTCUSDT,BCHUSDT,UNIUSDT,XLMUSDT,NEARUSDT,ATOMUSDT,APTUSDT"
).split(",")]

# ATR update interval in seconds
ATR_UPDATE_INTERVAL_SEC = int(os.getenv("ATR_UPDATE_INTERVAL_SEC", "60"))

# Minimum number of 5m candles required for a real ATR calculation
ATR_MIN_CANDLES = 15


async def is_bot_running(redis_client) -> bool:
    status = await redis_client.get("bot:status")
    return status == "running"


async def fetch_5m_atr(pool: asyncpg.Pool, symbol: str) -> Optional[Decimal]:
    """
    Query TimescaleDB and calculate ATR(14) from 5-minute candles built
    directly from the `ticks` table.

    Returns the ATR as a Decimal in price units, or None if insufficient data.
    """
    query = """
        WITH candles AS (
            SELECT
                time_bucket('5 minutes', time) AS bucket,
                MAX(price) AS high,
                MIN(price) AS low,
                (array_agg(price ORDER BY time DESC))[1] AS close
            FROM ticks
            WHERE symbol = $1
              AND price > 0
              AND time > now() - INTERVAL '12 hours'
            GROUP BY bucket
            ORDER BY bucket ASC
        )
        SELECT high, low, close FROM candles
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, symbol)
    except Exception as e:
        logger.error("ATR DB query failed for %s: %s", symbol, e)
        return None

    if len(rows) < ATR_MIN_CANDLES:
        return None

    # Calculate True Range series
    true_ranges = []
    for i in range(1, len(rows)):
        high = float(rows[i]["high"])
        low = float(rows[i]["low"])
        prev_close = float(rows[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < 14:
        return None

    # Simple 14-period ATR (SMA of TR)
    atr_window = true_ranges[-14:]
    atr = sum(atr_window) / 14
    return Decimal(str(atr))


class RiskManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.open_positions: Dict[str, dict] = {}
        self.params = RiskParams()
        # ATR cache: symbol -> (atr_value: Decimal, is_real: bool)
        self.atr_cache: Dict[str, tuple[Decimal, bool]] = {}

    async def reload_params(self):
        self.params = await load_risk_params(self.redis_client)
        logger.info(
            "Risk params loaded: max_positions=%s max_daily_loss=%s "
            "commission_rate=%s min_profit_mult=%s",
            self.params.max_open_positions,
            self.params.max_daily_loss_usdt,
            self.params.commission_rate,
            self.params.min_profit_multiplier_vs_fees,
        )

    async def get_paper_balance(self, variant: str = "A") -> Decimal:
        val = await self.redis_client.get(f"paper:balance:{variant}")
        if not val:
            val = await self.redis_client.get("paper:balance")
        return Decimal(val) if val else Decimal(os.getenv("STARTING_CAPITAL", "100.0"))

    async def get_daily_metrics(self):
        today = time.strftime("%Y-%m-%d")
        pnl = await self.redis_client.get(f"risk:daily_pnl:{today}")
        losses = await self.redis_client.get(f"risk:consecutive_losses:{today}")
        return Decimal(pnl or "0"), int(losses or 0)

    async def update_daily_metrics(self, pnl_delta: Decimal, is_loss: bool):
        today = time.strftime("%Y-%m-%d")
        current_pnl, current_losses = await self.get_daily_metrics()

        new_pnl = current_pnl + pnl_delta
        new_losses = current_losses + 1 if is_loss else 0

        await self.redis_client.setex(f"risk:daily_pnl:{today}", 172800, str(new_pnl))
        await self.redis_client.setex(f"risk:consecutive_losses:{today}", 172800, str(new_losses))
        return new_pnl, new_losses

    def _get_atr(self, symbol: str, price: Decimal) -> tuple[Decimal, bool]:
        """
        Returns (atr_value, is_real_from_5m_candles).

        If no 5m ATR is cached for this symbol, falls back to min_atr_bps.
        Always logs clearly which mode is active.
        """
        if symbol in self.atr_cache:
            atr, is_real = self.atr_cache[symbol]
            return atr, is_real

        # Fallback: no 5m data available (cold-start or DB error)
        fallback_bps = self.params.min_atr_bps
        fallback_atr = price * (fallback_bps / Decimal("10000"))
        logger.warning(
            "⚠️ ATR FALLBACK ACTIVE for %s — using %.2f bps (min_atr_bps). "
            "5m cache not populated yet.",
            symbol,
            float(fallback_bps),
        )
        return fallback_atr, False

    async def check_circuit_breaker(self) -> bool:
        cb_state = await self.redis_client.hgetall("risk:circuit_breaker")
        if cb_state and cb_state.get("status") == "open":
            until = float(cb_state.get("until", 0))
            reason = cb_state.get("reason", "unknown")
            if time.time() < until:
                return True
            logger.warning(
                "Circuit breaker TIME EXPIRED — was: %s. "
                "Review phase1:events before trading resumes. Auto-closing CB.",
                reason,
            )
            await self.redis_client.hset(
                "risk:circuit_breaker",
                mapping={
                    "status": "closed",
                    "last_close_reason": f"time_expired: {reason}",
                    "closed_at": str(time.time()),
                },
            )
            await self.redis_client.lpush(
                "phase1:events",
                json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "circuit_breaker_auto_close",
                    "detail": f"time_expired; original_reason={reason}",
                }),
            )
        return False

    async def _phase1_reset_gate(self, reason: str) -> None:
        raw = await self.redis_client.get("phase1:gate")
        state = json.loads(raw) if raw else {}
        state["consecutive_clean_days"] = 0
        state["last_reset_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        state["last_reset_reason"] = reason
        await self.redis_client.set("phase1:gate", json.dumps(state))

    async def trigger_circuit_breaker(self, reason: str, duration_minutes: int = 1440):
        until = time.time() + (duration_minutes * 60)
        await self.redis_client.hset(
            "risk:circuit_breaker",
            mapping={
                "status": "open",
                "reason": reason,
                "until": str(until),
                "timestamp": str(time.time()),
            },
        )
        logger.error(f"CIRCUIT BREAKER OPENED: {reason}. Paused until {until}")
        await self.redis_client.lpush(
            "phase1:events",
            json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": "circuit_breaker_open",
                "detail": reason,
            }),
        )
        await self.redis_client.publish(
            "phase1:urgent",
            json.dumps({
                "event": "circuit_breaker_open",
                "message": f"Circuit breaker APERTO\nMotivo: {reason}\n\nNON resettare senza aver letto la causa.",
            }),
        )
        await self._phase1_reset_gate(f"circuit_breaker_open: {reason}")

    async def process_signal(self, signal: dict):
        hour_key = time.strftime("%Y-%m-%d:%H")
        await self.redis_client.incr(f"risk:stats:received:{hour_key}")
        await self.redis_client.expire(f"risk:stats:received:{hour_key}", 86400)

        if not await is_bot_running(self.redis_client):
            logger.debug("Signal ignored: bot is not running.")
            return

        if await self.check_circuit_breaker():
            logger.warning("Signal rejected: Circuit breaker is OPEN.")
            await self.redis_client.incr(f"risk:stats:rejected_other:{hour_key}")
            await self.redis_client.expire(f"risk:stats:rejected_other:{hour_key}", 86400)
            return

        p = self.params
        symbol = signal["symbol"]
        price = Decimal(str(signal["price"]))
        direction = signal["type"]

        pos_key = f"{symbol}_{signal.get('ab_variant', 'A')}"
        if len(self.open_positions) >= p.max_open_positions and pos_key not in self.open_positions:
            logger.warning(f"Signal rejected: Max open positions reached ({p.max_open_positions})")
            await self.redis_client.incr(f"risk:stats:rejected_other:{hour_key}")
            await self.redis_client.expire(f"risk:stats:rejected_other:{hour_key}", 86400)
            return

        # --- ATR: 5m candle-based (from cache) or fallback ---
        atr, is_real_atr = self._get_atr(symbol, price)

        atr_bps = (atr / price) * 10000
        atr_source = "5m-candle" if is_real_atr else f"FALLBACK-{float(p.min_atr_bps)}bps"
        logger.info(
            "ATR for %s: %.2f bps (source: %s)",
            symbol,
            float(atr_bps),
            atr_source,
        )

        # --- Volatility filter: reject if ATR too low ---
        if atr_bps < p.min_atr_bps:
            msg = (
                f"\U0001f6ab Signal REJECTED: {symbol} {direction}\n"
                f"Reason: Low Volatility\n"
                f"ATR: {float(atr_bps):.2f} bps < min {float(p.min_atr_bps):.1f} bps\n"
                f"ATR source: {atr_source}"
            )
            logger.warning(msg)
            await self.redis_client.incr(f"risk:stats:rejected_low_volatility:{hour_key}")
            await self.redis_client.expire(f"risk:stats:rejected_low_volatility:{hour_key}", 86400)
            await self.redis_client.publish(
                "alerts:telegram",
                json.dumps({"event": "risk_filter", "message": msg}),
            )
            return

        balance = await self.get_paper_balance(signal.get("ab_variant", "A"))
        risk_amount = balance * p.risk_per_trade_pct
        sl_distance = atr * p.stop_loss_atr_multiplier

        if sl_distance == 0:
            return

        qty = risk_amount / sl_distance
        exposure = qty * price
        if exposure > p.max_exposure_per_symbol_usdt:
            qty = p.max_exposure_per_symbol_usdt / price
            logger.info(f"Capped {symbol} exposure to {p.max_exposure_per_symbol_usdt} USDT")

        if direction == "BUY":
            sl_price = price - (atr * p.stop_loss_atr_multiplier)
            tp_price = price + (atr * p.take_profit_atr_multiplier)
        else:
            sl_price = price + (atr * p.stop_loss_atr_multiplier)
            tp_price = price - (atr * p.take_profit_atr_multiplier)

        expected_gross_profit = abs(tp_price - price) * qty
        total_commissions = (price * qty * p.commission_rate) + (tp_price * qty * p.commission_rate)

        if expected_gross_profit < (total_commissions * p.min_profit_multiplier_vs_fees):
            msg = (
                f"🚫 Signal REJECTED: {symbol} {direction}\n"
                f"Reason: Low Profitability\nExp. Profit: ${expected_gross_profit:.4f}\n"
                f"Total Fees: ${total_commissions:.4f}\n"
                f"ATR source: {atr_source} ({float(atr_bps):.2f} bps)"
            )
            logger.warning(msg)
            await self.redis_client.incr(f"risk:stats:rejected_low_profit:{hour_key}")
            await self.redis_client.expire(f"risk:stats:rejected_low_profit:{hour_key}", 86400)
            await self.redis_client.publish(
                "alerts:telegram",
                json.dumps({"event": "risk_filter", "message": msg}),
            )
            return

        trailing_distance = sl_distance * Decimal(os.getenv("TRAILING_ATR_FRACTION", "0.5"))

        # --- LIMIT ENTRY ON PULLBACK ---
        # Place limit order at entry_pullback_bps below the signal price (BUY) or above (SELL).
        # SL/TP are anchored to this limit price for correct R:R.
        pullback_offset = price * (p.entry_pullback_bps / Decimal("10000"))
        if direction == "BUY":
            limit_price = price - pullback_offset
            sl_price = limit_price - (atr * p.stop_loss_atr_multiplier)
            tp_price = limit_price + (atr * p.take_profit_atr_multiplier)
        else:
            limit_price = price + pullback_offset
            sl_price = limit_price + (atr * p.stop_loss_atr_multiplier)
            tp_price = limit_price - (atr * p.take_profit_atr_multiplier)

        command = {
            "command_id": str(uuid.uuid4()),
            "type": direction,
            "symbol": symbol,
            "price": str(limit_price),       # This is now the LIMIT entry price
            "signal_price": str(price),       # Original signal price (for logging)
            "quantity": str(qty),
            "stop_loss_price": str(sl_price),
            "take_profit_price": str(tp_price),
            "trailing_stop_distance": str(trailing_distance),
            "timestamp_ms": int(time.time() * 1000),
            "strategy": signal.get("strategy_name", "Unknown"),
            "ab_variant": signal.get("ab_variant", "A"),
            "atr_source": atr_source,
            "atr_bps": float(atr_bps),
            "entry_pullback_bps": float(p.entry_pullback_bps),
            "pending_order_timeout_seconds": p.pending_order_timeout_seconds,
        }

        self.open_positions[pos_key] = command
        logger.info(
            f"Signal APPROVED [{command['ab_variant']}]: {command['type']} {command['symbol']} "
            f"Qty: {qty:.4f} | Signal@{price} → Limit@{limit_price:.4f} (-{float(p.entry_pullback_bps):.1f}bps) "
            f"(SL: {sl_price:.4f}, TP: {tp_price:.4f}) [ATR: {float(atr_bps):.2f} bps from {atr_source}]"
        )
        await self.redis_client.incr(f"risk:stats:approved:{hour_key}")
        await self.redis_client.expire(f"risk:stats:approved:{hour_key}", 86400)
        await self.redis_client.publish(f"approved_orders:{symbol}", json.dumps(command))
        await self.redis_client.publish("approved_orders", json.dumps(command))

    async def handle_trade_execution(self, trade: dict):
        symbol = trade.get("symbol", "UNKNOWN")
        pos_key = f"{symbol}_{trade.get('ab_variant', 'A')}"
        if pos_key in self.open_positions:
            del self.open_positions[pos_key]

        pnl = Decimal(str(trade.get("pnl_usdt", 0)))
        new_pnl, new_losses = await self.update_daily_metrics(pnl, pnl < 0)
        p = self.params

        if new_pnl < -p.max_daily_loss_usdt:
            await self.trigger_circuit_breaker(f"Max Daily Loss Reached ({new_pnl:.2f} USDT)")
        elif new_losses >= p.max_consecutive_losses:
            await self.trigger_circuit_breaker(
                f"{p.max_consecutive_losses} Consecutive Losses",
                p.consecutive_loss_pause_minutes,
            )


async def atr_updater_loop(rm: RiskManager, pool: asyncpg.Pool):
    """
    Background task: every ATR_UPDATE_INTERVAL_SEC seconds, fetches the
    5m-candle ATR from TimescaleDB for every watched symbol and warms up
    (or refreshes) the in-memory atr_cache.

    On DB failure: retries with exponential backoff (max 5 min).
    Tracks fallback hours so the Telegram report can surface them.
    """
    logger.info(
        "⏳ ATR cache warming up — fallback active for all symbols until first DB fetch completes."
    )

    backoff = 5          # seconds, doubles on each failure
    max_backoff = 300    # 5 minutes
    fallback_start: Optional[float] = None  # when did we go into fallback

    while True:
        try:
            ready_symbols = {}
            fallback_symbols = []

            for symbol in WATCHED_SYMBOLS:
                atr = await fetch_5m_atr(pool, symbol)
                if atr is not None:
                    was_fallback = (
                        symbol not in rm.atr_cache or not rm.atr_cache[symbol][1]
                    )
                    rm.atr_cache[symbol] = (atr, True)

                    try:
                        tick_raw = await rm.redis_client.get(f"tick:last:{symbol}")
                        if tick_raw:
                            tick = json.loads(tick_raw)
                            price = Decimal(str(tick["price"]))
                            atr_bps = (atr / price) * 10000
                            ready_symbols[symbol] = float(atr_bps)
                        else:
                            ready_symbols[symbol] = None
                    except Exception:
                        ready_symbols[symbol] = None

                    if was_fallback:
                        bps_str = f"{ready_symbols[symbol]:.2f}" if ready_symbols[symbol] else "N/A"
                        logger.info(
                            "✅ ATR cache populated for %s: %s bps (5m-candle, source: TimescaleDB)",
                            symbol,
                            bps_str,
                        )
                else:
                    fallback_symbols.append(symbol)

            # Log the cache-ready summary
            cache_summary_parts = []
            for sym, bps in ready_symbols.items():
                bps_str = f"{bps:.2f}" if bps is not None else "N/A"
                cache_summary_parts.append(f"{sym}={bps_str} bps")

            if cache_summary_parts:
                logger.info("✅ ATR cache ready: %s", ", ".join(cache_summary_parts))

            if fallback_symbols:
                logger.warning(
                    "⚠️ ATR FALLBACK ACTIVE: insufficient 5m candles for: %s",
                    ", ".join(fallback_symbols),
                )
                if fallback_start is None:
                    fallback_start = time.time()
                    await rm.redis_client.set("risk:atr_fallback_since", str(fallback_start))
            else:
                # All symbols have real ATR — if we were in fallback, log recovery
                if fallback_start is None:
                    redis_fallback = await rm.redis_client.get("risk:atr_fallback_since")
                    if redis_fallback:
                        try:
                            fallback_start = float(redis_fallback)
                        except ValueError:
                            pass

                if fallback_start is not None:
                    duration_sec = time.time() - fallback_start
                    logger.info(
                        "✅ ATR cache reconnected after %.0f seconds of fallback.",
                        duration_sec,
                    )
                    await rm.redis_client.set(
                        "risk:atr_last_fallback_duration_sec", str(int(duration_sec))
                    )
                    await rm.redis_client.delete("risk:atr_fallback_since")
                    fallback_start = None

            # Reset backoff on success
            backoff = 5
            await asyncio.sleep(ATR_UPDATE_INTERVAL_SEC)

        except Exception as e:
            logger.error(
                "❌ ATR updater loop DB error: %s — retrying in %ds", e, backoff
            )
            if fallback_start is None:
                fallback_start = time.time()
                await rm.redis_client.set("risk:atr_fallback_since", str(fallback_start))
                logger.warning("⚠️ ATR FALLBACK ACTIVE — DB unreachable, will retry with backoff.")
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)


async def reconciliation_loop(rm: RiskManager):
    logger.info("Reconciliation loop started.")
    pool = await asyncpg.create_pool(dsn=DB_DSN)

    while True:
        try:
            await asyncio.sleep(60)

            async with pool.acquire() as conn:
                db_positions = await conn.fetch("SELECT symbol, ab_variant FROM positions")
                db_keys = {f"{r['symbol']}_{r['ab_variant']}" for r in db_positions}

                for db_key in db_keys:
                    if db_key not in rm.open_positions:
                        logger.error(
                            f"RECONCILIATION FAILURE: Position {db_key} found in DB but missing from memory!"
                        )
                        await rm.redis_client.publish(
                            "alerts:telegram",
                            json.dumps({
                                "event": "reconciliation_error",
                                "message": (
                                    f"⚠️ RECONCILIATION ERROR\nPosition {db_key} is in DB "
                                    "but missing from RiskManager state.\nPotential logic desync!"
                                ),
                            }),
                        )

                for mem_key in rm.open_positions.keys():
                    if mem_key not in db_keys:
                        logger.error(
                            f"RECONCILIATION FAILURE: Position {mem_key} in memory but NOT in DB!"
                        )

            hb_str = await rm.redis_client.get("ingestion:heartbeat")
            if hb_str:
                age = (time.time() * 1000) - int(hb_str)
                if age > 10000:
                    logger.warning(f"STALE DATA DETECTED: Last heartbeat {age}ms ago. Entering SAFE MODE.")
                    await rm.redis_client.set("bot:safe_mode", "1")
                else:
                    await rm.redis_client.delete("bot:safe_mode")

        except Exception as e:
            logger.error(f"Reconciliation loop error: {e}")
            await asyncio.sleep(10)


async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    # No longer subscribing to ticks:* — ATR is computed on 5m candles from DB
    await pubsub.psubscribe("signals:*", "executed_trades", "system:commands")

    rm = RiskManager(redis_client)
    await rm.reload_params()

    # Start DB connection pool and background ATR updater
    pool = await asyncpg.create_pool(dsn=DB_DSN)
    asyncio.create_task(atr_updater_loop(rm, pool))
    asyncio.create_task(reconciliation_loop(rm))

    logger.info("Risk Manager started. Listening for signals (ATR from 5m candles)...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            try:
                if channel == "system:commands":
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    if data == "RELOAD_CONFIG":
                        await rm.reload_params()
                    continue

                data = json.loads(message["data"])
                if channel.startswith("signals:"):
                    safe_mode = await redis_client.get("bot:safe_mode")
                    if safe_mode:
                        logger.warning("Signal rejected: Bot is in SAFE MODE due to stale data.")
                        continue
                    await rm.process_signal(data)
                elif channel == "executed_trades":
                    await rm.handle_trade_execution(data)
            except Exception as e:
                logger.error(f"Error processing message from {channel}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
