import asyncio
import json
import logging
import os
import time
import uuid
import random
from typing import Dict, Any
from decimal import Decimal, getcontext
import redis.asyncio as redis
import asyncpg

getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
DB_DSN = f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:{os.getenv('DB_PASSWORD', 'crypto_pass')}@{os.getenv('DB_HOST', 'timescaledb')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'cryptoscalper_db')}"
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
MAX_TRADE_DURATION_SECONDS = int(os.getenv("MAX_TRADE_DURATION_SECONDS", 300))
PENDING_ORDER_TIMEOUT_SECONDS = int(os.getenv("PENDING_ORDER_TIMEOUT_SECONDS", 300))


async def is_bot_running(redis_client) -> bool:
    status = await redis_client.get("bot:status")
    return status == "running"

class OrderCommand:
    def __init__(self, data: dict):
        self.command_id = data.get("command_id", str(uuid.uuid4()))
        self.symbol = data["symbol"]
        self.direction = data["type"] # BUY or SELL
        self.target_price = Decimal(str(data["price"]))  # This is the LIMIT entry price
        self.signal_price = Decimal(str(data.get("signal_price", data["price"])))
        self.quantity = Decimal(str(data["quantity"]))
        self.stop_loss = Decimal(str(data["stop_loss_price"])) if data.get("stop_loss_price") else None
        self.take_profit = Decimal(str(data["take_profit_price"])) if data.get("take_profit_price") else None
        self.strategy = data.get("strategy", "Unknown")
        self.ab_variant = data.get("ab_variant", "A")
        self.trailing_distance = (
            Decimal(str(data["trailing_stop_distance"])) if data.get("trailing_stop_distance") else None
        )
        self.pending_order_timeout = int(data.get("pending_order_timeout_seconds", PENDING_ORDER_TIMEOUT_SECONDS))
        self.created_at = time.time()
        self.status = "PENDING"
        self.executed_price = Decimal("0")
        self.peak_price = Decimal("0")
        self.breakeven_set: bool = False
        # ATR in basis points passed from RiskManager in approved_orders payload (float bps)
        self.atr_bps: float | None = data.get("atr_bps")
        
    def to_dict(self):
        return {
            "command_id": self.command_id,
            "symbol": self.symbol,
            "type": self.direction,
            "price": str(self.target_price),
            "quantity": str(self.quantity),
            "stop_loss_price": str(self.stop_loss) if self.stop_loss else None,
            "take_profit_price": str(self.take_profit) if self.take_profit else None,
            "strategy": self.strategy,
            "ab_variant": self.ab_variant,
            "status": self.status,
            "executed_price": str(self.executed_price),
            "created_at": self.created_at
        }

class PaperEngine:
    def __init__(self, redis_client, db_pool):
        self.redis_client = redis_client
        self.db_pool = db_pool
        self.open_positions: Dict[str, OrderCommand] = {}   # symbol_variant -> filled cmd
        self.pending_orders: Dict[str, OrderCommand] = {}   # symbol_variant -> pending cmd
        self.paper_balances = {
            "A": Decimal("100"),
            "B": Decimal("100")
        }
        # Simple ATR estimator per symbol (uses recent true ranges)
        from collections import defaultdict, deque

        self.atr_windows: dict[str, deque[Decimal]] = defaultdict(lambda: deque(maxlen=14))
        self.atr_by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        self.last_price_by_symbol: dict[str, Decimal] = {}

    async def publish_order_event(self, *, status: str, pos_key: str, cmd: OrderCommand, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "pos_key": pos_key,
            "command_id": cmd.command_id,
            "symbol": cmd.symbol,
            "ab_variant": cmd.ab_variant,
            "side": cmd.direction,
            "quantity": float(cmd.quantity),
            "strategy_name": cmd.strategy,
            "created_at_ts": cmd.created_at,
        }
        if extra:
            payload.update(extra)
        await self.redis_client.publish("order_events", json.dumps(payload))

    async def init_ledger(self):
        balance_a = await self.redis_client.get("paper:balance:A")
        if balance_a:
            self.paper_balances["A"] = Decimal(balance_a)
        else:
            await self.redis_client.set("paper:balance:A", str(self.paper_balances["A"]))
            
        balance_b = await self.redis_client.get("paper:balance:B")
        if balance_b:
            self.paper_balances["B"] = Decimal(balance_b)
        else:
            await self.redis_client.set("paper:balance:B", str(self.paper_balances["B"]))

        # Caricamento posizioni aperte dal DB
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM positions")
                for row in rows:
                    cmd = OrderCommand({
                        "symbol": row["symbol"],
                        "type": row["side"],
                        "price": str(row["entry_price"]),
                        "quantity": str(row["quantity"]),
                        "stop_loss_price": str(row["stop_loss"]),
                        "take_profit_price": str(row["take_profit"]),
                        "ab_variant": row["ab_variant"]
                    })
                    cmd.status = "FILLED"
                    cmd.executed_price = row["entry_price"]
                    # Reset created_at to NOW so reloaded positions don't
                    # immediately TIMEOUT on restart
                    cmd.created_at = time.time()
                    pos_key = f"{cmd.symbol}_{cmd.ab_variant}"
                    # load breakeven flag and atr_bps from Redis if present
                    try:
                        val = await self.redis_client.get(f"position:breakeven:{pos_key}")
                        cmd.breakeven_set = bool(int(val)) if val is not None else False
                    except Exception:
                        cmd.breakeven_set = False
                    try:
                        atv = await self.redis_client.get(f"position:atr_bps:{pos_key}")
                        if atv is not None:
                            cmd.atr_bps = float(atv)
                    except Exception:
                        pass
                    self.open_positions[pos_key] = cmd
                logger.info(f"Loaded {len(rows)} open positions from DB.")
        except Exception as e:
            logger.error(f"Error loading positions from DB: {e}")

    async def save_order_to_db(self, cmd: OrderCommand):
        try:
            async with self.db_pool.acquire() as conn:
                # Update orders history
                await conn.execute("""
                    INSERT INTO orders (id, symbol, side, price, quantity, status, strategy, ab_variant)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
                """, uuid.UUID(cmd.command_id), cmd.symbol, cmd.direction, cmd.target_price, cmd.quantity, cmd.status, cmd.strategy, cmd.ab_variant)
                
                # Update active positions table
                if cmd.status == "FILLED":
                    await conn.execute("""
                        INSERT INTO positions (symbol, ab_variant, entry_time, entry_price, quantity, side, stop_loss, take_profit)
                        VALUES ($1, $2, NOW(), $3, $4, $5, $6, $7)
                        ON CONFLICT (symbol, ab_variant) DO NOTHING
                    """, cmd.symbol, cmd.ab_variant, cmd.executed_price, cmd.quantity, cmd.direction, cmd.stop_loss, cmd.take_profit)
        except Exception as e:
            logger.error(f"DB Error saving order/position: {e}")

    async def process_new_command(self, cmd_data: dict):
        if not await is_bot_running(self.redis_client):
            logger.info("Order ignored: bot is not running.")
            return

        cmd = OrderCommand(cmd_data)

        # 1. Idempotency Check (Persistent on Redis)
        lock_key = f"exec:lock:{cmd.command_id}"
        if not await self.redis_client.setnx(lock_key, "1"):
            logger.warning(f"Duplicate command ignored (Redis Lock): {cmd.command_id}")
            return
        await self.redis_client.expire(lock_key, 86400) # 24h

        pos_key = f"{cmd.symbol}_{cmd.ab_variant}"

        # 2. Reject if already have an open position or pending order on this key
        if pos_key in self.open_positions:
            logger.info(f"Order rejected: already have open position for {pos_key}")
            return
        if pos_key in self.pending_orders:
            logger.info(f"Order rejected: already have pending order for {pos_key}")
            return

        # 3. Persist as PENDING in DB
        await self.save_order_to_db(cmd)

        # 4. Track pending stats in Redis
        day_key = time.strftime("%Y-%m-%d")
        await self.redis_client.incr(f"pending:stats:placed:{day_key}")
        await self.redis_client.expire(f"pending:stats:placed:{day_key}", 172800)

        # 5. Park in pending queue — monitor_ticks will fill or expire it
        self.pending_orders[pos_key] = cmd
        logger.info(
            f"LIMIT ORDER PENDING [{cmd.ab_variant}]: {cmd.direction} {cmd.quantity:.4f} {cmd.symbol} "
            f"| Signal@{cmd.signal_price:.4f} → Limit@{cmd.target_price:.4f} "
            f"| Timeout: {cmd.pending_order_timeout}s"
        )

    async def fill_pending_order(self, pos_key: str, cmd: OrderCommand, fill_price: Decimal):
        """Transition a pending order to FILLED and open a live position."""
        cmd.status = "FILLED"
        cmd.executed_price = fill_price
        cmd.peak_price = fill_price
        cmd.breakeven_set = False

        await self.save_order_to_db(cmd)

        # Persist atr_bps to Redis for later recovery (if provided)
        try:
            if getattr(cmd, "atr_bps", None) is not None:
                await self.redis_client.set(f"position:atr_bps:{pos_key}", str(cmd.atr_bps))
        except Exception:
            pass

        del self.pending_orders[pos_key]
        self.open_positions[pos_key] = cmd

        # persist initial breakeven flag (not set)
        try:
            await self.redis_client.set(f"position:breakeven:{pos_key}", "0")
        except Exception:
            pass

        # Track fill stats
        day_key = time.strftime("%Y-%m-%d")
        await self.redis_client.incr(f"pending:stats:filled:{day_key}")
        await self.redis_client.expire(f"pending:stats:filled:{day_key}", 172800)

        logger.info(
            f"LIMIT ORDER FILLED [{cmd.ab_variant}]: {cmd.direction} {cmd.quantity:.4f} {cmd.symbol} "
            f"@ {fill_price:.4f} (limit was {cmd.target_price:.4f})"
        )
        await self.publish_order_event(
            status="FILLED",
            pos_key=pos_key,
            cmd=cmd,
            extra={
                "fill_price": float(fill_price),
                "limit_price": float(cmd.target_price),
                "filled_at_ts": time.time(),
            },
        )

    async def expire_pending_order(self, pos_key: str, cmd: OrderCommand, reason: str):
        """Cancel a pending order that expired without being filled."""
        cmd.status = "CANCELLED"
        await self.save_order_to_db(cmd)
        del self.pending_orders[pos_key]

        # Track cancellation stats
        day_key = time.strftime("%Y-%m-%d")
        if reason == "TIMEOUT":
            await self.redis_client.incr(f"pending:stats:cancelled:{day_key}")
            await self.redis_client.expire(f"pending:stats:cancelled:{day_key}", 172800)
        else:
            await self.redis_client.incr(f"pending:stats:escaped:{day_key}")
            await self.redis_client.expire(f"pending:stats:escaped:{day_key}", 172800)

        logger.info(f"LIMIT ORDER {reason} [{cmd.ab_variant}]: {cmd.symbol} | Limit was {cmd.target_price:.4f}")
        # Pubblica su order_events SOLO gli status richiesti:
        # - TIMEOUT (scaduto)
        # - CANCELLED (tutte le altre cancellazioni/annullamenti)
        status_event = "TIMEOUT" if str(reason).upper() == "TIMEOUT" else "CANCELLED"
        await self.publish_order_event(
            # Publish the actual outcome to order_events without touching
            # executed_trades (kept backward-compatible).
            status=status_event,
            pos_key=pos_key,
            cmd=cmd,
            extra={
                "cancel_reason": reason,
                "limit_price": float(cmd.target_price),
            },
        )

    async def close_position(self, pos_key: str, close_price: Decimal, reason: str):
        if pos_key not in self.open_positions:
            return
            
        pos = self.open_positions[pos_key]
        COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))

        if pos.direction == "BUY":
            pnl_usdt = (close_price - pos.executed_price) * pos.quantity
        else:
            pnl_usdt = (pos.executed_price - close_price) * pos.quantity
            
        fee = (pos.executed_price * pos.quantity * COMMISSION_RATE) + (close_price * pos.quantity * COMMISSION_RATE)
        net_pnl = pnl_usdt - fee

        # Update paper balance persistente
        self.paper_balances[pos.ab_variant] += net_pnl
        await self.redis_client.set(f"paper:balance:{pos.ab_variant}", str(self.paper_balances[pos.ab_variant]))
        
        # Rimuovi posizione dal DB
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM positions WHERE symbol = $1 AND ab_variant = $2", pos.symbol, pos.ab_variant)
        except Exception as e:
            logger.error(f"Error deleting position from DB: {e}")

        pnl_pct = (net_pnl / (pos.executed_price * pos.quantity)) * 100
        
        trade_record = {
            "symbol": pos.symbol,
            "side": pos.direction,
            "price": round(float(close_price), 4),
            "entry_price": round(float(pos.executed_price), 4),
            "exit_price": round(float(close_price), 4),
            "quantity": round(float(pos.quantity), 6),
            "pnl_usdt": round(float(net_pnl), 2),
            "pnl_pct": round(float(pnl_pct), 2),
            "close_reason": reason,
            "ab_variant": pos.ab_variant,
            "strategy_name": pos.strategy,
            "open_time_ts": pos.created_at,  # Unix timestamp of actual entry
        }
        
        await self.redis_client.publish("executed_trades", json.dumps(trade_record))
        # Also publish the same payload on order_events (with status/pos_key)
        # so stateful consumers can rely on a single stream.
        await self.publish_order_event(
            status="FILLED",
            pos_key=pos_key,
            cmd=pos,
            extra=trade_record,
        )
        del self.open_positions[pos_key]
        logger.info(f"POSITION CLOSED [{pos.ab_variant}]: {pos.symbol} @ {close_price} | PNL: {net_pnl:.2f} ({pnl_pct:.2f}%)")


    async def monitor_ticks(self, tick: dict):
        symbol = tick["symbol"]
        price = Decimal(str(tick["price"]))
        now = time.time()

        # --- Update simple ATR estimate ---
        try:
            last = self.last_price_by_symbol.get(symbol)
            if last is not None:
                tr = abs(price - last)
                self.atr_windows[symbol].append(tr)
                # mean true range
                window = self.atr_windows[symbol]
                if len(window) > 0:
                    self.atr_by_symbol[symbol] = sum(window) / Decimal(len(window))
            self.last_price_by_symbol[symbol] = price
        except Exception:
            pass

        # --- CHECK PENDING LIMIT ORDERS ---
        for variant in ["A", "B"]:
            pos_key = f"{symbol}_{variant}"
            if pos_key not in self.pending_orders:
                continue

            cmd = self.pending_orders[pos_key]
            elapsed = now - cmd.created_at

            # Check expiry first
            if elapsed > cmd.pending_order_timeout:
                await self.expire_pending_order(pos_key, cmd, "TIMEOUT")
                continue

            # Check fill condition
            if cmd.direction == "BUY" and price <= cmd.target_price:
                await self.fill_pending_order(pos_key, cmd, price)
            elif cmd.direction == "SELL" and price >= cmd.target_price:
                await self.fill_pending_order(pos_key, cmd, price)

        # --- MANAGE OPEN POSITIONS ---
        for variant in ["A", "B"]:
            pos_key = f"{symbol}_{variant}"
            if pos_key not in self.open_positions:
                continue
                
            pos = self.open_positions[pos_key]

            # --- BREAKEVEN STOP LOGIC ---
            try:
                if not getattr(pos, "breakeven_set", False):
                    # Prefer ATR passed in payload (atr_bps) if available
                    atr_price = Decimal("0")
                    if getattr(pos, "atr_bps", None) is not None:
                        try:
                            atr_price = (pos.executed_price * Decimal(str(pos.atr_bps))) / Decimal("10000")
                        except Exception:
                            atr_price = Decimal("0")
                    else:
                        atr_price = self.atr_by_symbol.get(symbol, Decimal("0"))

                    # require atr > 0 to avoid spurious triggers
                    if atr_price > 0:
                        # BUY: price exceeds entry + 1.0 * ATR
                        if pos.direction == "BUY" and price >= pos.executed_price + atr_price:
                            # compute fee round trip bps
                            COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))
                            fee_bps = Decimal(os.getenv("FEE_ROUND_TRIP_BPS", str((COMMISSION_RATE * 2 * Decimal('10000')).quantize(Decimal('1')))))
                            fee_offset = (pos.executed_price * fee_bps) / Decimal("10000")
                            new_sl = pos.executed_price + fee_offset
                            if pos.stop_loss is None or new_sl > pos.stop_loss:
                                pos.stop_loss = new_sl
                            pos.breakeven_set = True
                        # SELL: price below entry - 1.0 * ATR
                        if pos.direction == "SELL" and price <= pos.executed_price - atr_price:
                            COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))
                            fee_bps = Decimal(os.getenv("FEE_ROUND_TRIP_BPS", str((COMMISSION_RATE * 2 * Decimal('10000')).quantize(Decimal('1')))))
                            fee_offset = (pos.executed_price * fee_bps) / Decimal("10000")
                            new_sl = pos.executed_price - fee_offset
                            if pos.stop_loss is None or new_sl < pos.stop_loss:
                                pos.stop_loss = new_sl
                            pos.breakeven_set = True

                    # if breakeven just set, persist to DB and Redis and log
                    if getattr(pos, "breakeven_set", False):
                        try:
                            await self.redis_client.set(f"position:breakeven:{pos_key}", "1")
                            await self.redis_client.set(f"position:stop_loss:{pos_key}", str(pos.stop_loss) if pos.stop_loss is not None else "")
                        except Exception:
                            pass
                        try:
                            async with self.db_pool.acquire() as conn:
                                await conn.execute("UPDATE positions SET stop_loss = $1 WHERE symbol = $2 AND ab_variant = $3", pos.stop_loss, pos.symbol, pos.ab_variant)
                        except Exception as e:
                            logger.error(f"DB Error updating breakeven stop: {e}")

                        logger.info(f"BREAKEVEN STOP SET [{pos.symbol}]: nuovo SL = {pos.stop_loss}")
            except Exception:
                logger.exception("Error processing breakeven logic")

            if pos.trailing_distance and pos.trailing_distance > 0:
                if pos.direction == "BUY":
                    if price > pos.peak_price:
                        pos.peak_price = price
                        new_sl = price - pos.trailing_distance
                        if pos.stop_loss is None or new_sl > pos.stop_loss:
                            pos.stop_loss = new_sl
                else:
                    if price < pos.peak_price or pos.peak_price <= 0:
                        pos.peak_price = price
                        new_sl = price + pos.trailing_distance
                        if pos.stop_loss is None or new_sl < pos.stop_loss:
                            pos.stop_loss = new_sl

            if pos.stop_loss:
                if (pos.direction == "BUY" and price <= pos.stop_loss) or \
                   (pos.direction == "SELL" and price >= pos.stop_loss):
                    await self.close_position(pos_key, price, "SL_HIT")
                    continue
                    
            if pos.take_profit:
                if (pos.direction == "BUY" and price >= pos.take_profit) or \
                   (pos.direction == "SELL" and price <= pos.take_profit):
                    await self.close_position(pos_key, price, "TP_HIT")
                    continue
                    
            if time.time() - pos.created_at > MAX_TRADE_DURATION_SECONDS:
                await self.close_position(pos_key, price, "TIMEOUT")

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    db_pool = await asyncpg.create_pool(dsn=DB_DSN)

    if PAPER_TRADING:
        engine = PaperEngine(redis_client, db_pool)
        await engine.init_ledger()
        mode_label = "PAPER"
    else:
        from live_engine import LiveEngine

        engine = LiveEngine(redis_client, db_pool)
        await engine.bootstrap()
        mode_label = "LIVE_SCAFFOLD"

    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("approved_orders:*", "ticks:*", "approved_orders")

    logger.info("%s execution engine started. Monitoring...", mode_label)

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            try:
                data = json.loads(message["data"])
                if channel.startswith("ticks:"):
                    await engine.monitor_ticks(data)
                elif "approved_orders" in channel:
                    asyncio.create_task(engine.process_new_command(data))
            except Exception as e:
                logger.error(f"Error in main loop: {e}")

if __name__ == "__main__":
    asyncio.run(main())

