import asyncio
import json
import logging
import os
import sys
import uuid
import time
import redis.asyncio as redis
from abc import ABC, abstractmethod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", 0.001)) # 0.1% defaults
open_positions = {} # symbol -> list of position dicts

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class Command(ABC):
    @abstractmethod
    async def execute(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

async def execute_market_order(order_data: dict, redis_client):
    logger.info(f"Executing Paper Trade Entry: {order_data}")

    symbol = order_data.get("symbol", "")
    last_tick_raw = await redis_client.get(f"tick:last:{symbol}")

    exec_price = float(order_data.get("suggested_price", 0))
    if last_tick_raw:
        try:
            last_tick = json.loads(last_tick_raw)
            exec_price = float(last_tick.get("price", exec_price))
        except Exception:
            pass

    # Simulate network latency (50ms - 150ms)
    import random
    await asyncio.sleep(random.uniform(0.05, 0.15))

    if exec_price <= 0:
        logger.error("Invalid execution price. Aborting.")
        return

    qty = float(order_data.get("suggested_qty", 0.01))
    side = order_data.get("direction", "BUY").upper()

    position_id = str(uuid.uuid4())

    # 0.5% default SL/TP for scalping demo
    sl_mult = 0.995 if side == "BUY" else 1.005
    tp_mult = 1.005 if side == "BUY" else 0.995

    position = {
        "id": position_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": exec_price,
        "entry_time": int(time.time() * 1000),
        "stop_loss": exec_price * sl_mult,
        "take_profit": exec_price * tp_mult,
        "strategy": order_data.get("strategy_name", "Unknown")
    }

    if symbol not in open_positions:
        open_positions[symbol] = []
    open_positions[symbol].append(position)
    logger.info(f"Position opened: {position}")

async def evaluate_open_positions(tick: dict, redis_client):
    symbol = tick.get("symbol")
    if symbol not in open_positions or not open_positions[symbol]:
        return

    current_price = float(tick.get("price", 0))
    if current_price <= 0:
        return

    remaining_positions = []
    for pos in open_positions[symbol]:
        side = pos["side"]
        sl = pos["stop_loss"]
        tp = pos["take_profit"]

        hit_sl = (side == "BUY" and current_price <= sl) or (side == "SELL" and current_price >= sl)
        hit_tp = (side == "BUY" and current_price >= tp) or (side == "SELL" and current_price <= tp)
        timeout = (int(time.time() * 1000) - pos["entry_time"]) > 300000 # 5 min timeout

        if hit_sl or hit_tp or timeout:
            reason = "SL_HIT" if hit_sl else "TP_HIT" if hit_tp else "TIMEOUT"
            logger.info(f"Closing position {pos['id']} due to {reason} at {current_price}")

            entry_value = pos["entry_price"] * pos["qty"]
            exit_value = current_price * pos["qty"]

            gross_pnl = (exit_value - entry_value) if side == "BUY" else (entry_value - exit_value)
            commissions = (entry_value * COMMISSION_RATE) + (exit_value * COMMISSION_RATE)
            net_pnl = gross_pnl - commissions

            result = {
                "status": "FILLED",
                "order": {
                    "symbol": symbol,
                    "type": "SELL" if side == "BUY" else "BUY",
                    "price": current_price,
                    "quantity": pos["qty"],
                    "strategy": pos["strategy"]
                },
                "close_reason": reason,
                "gross_pnl": gross_pnl,
                "commission_paid": commissions,
                "pnl_netto": net_pnl
            }

            # Update virtual balance in Redis
            try:
                balance_raw = await redis_client.get("paper:balance")
                current_balance = float(balance_raw) if balance_raw else 10000.0 # Start with $10k default
                new_balance = current_balance + net_pnl
                await redis_client.set("paper:balance", new_balance)

                # Broadcast updated balance
                await redis_client.publish("paper:balance_updates", json.dumps({"balance": new_balance, "timestamp": int(time.time() * 1000)}))
            except Exception as e:
                logger.error(f"Failed to update paper balance: {e}")

            await redis_client.publish("executed_trades", json.dumps(result))
        else:
            remaining_positions.append(pos)

    open_positions[symbol] = remaining_positions

async def main():
    # PAPER_TRADING safety guard constraint
    paper_trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
    if paper_trading_mode != "PAPER":
        logger.critical("REAL TRADING NOT IMPLEMENTED — set TRADING_MODE=PAPER (or PAPER_TRADING=true)")
        sys.exit(1)

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("approved_orders", "shadow_orders", "ticks:*")
    logger.info("Paper Trading Engine started. Listening for orders, shadow orders, and live ticks...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message.get("channel", "")
            data = json.loads(message["data"])

            if channel in ["approved_orders", "shadow_orders"]:
                try:
                    # In a fully fleshed out system, shadow_orders might have their own isolated
                    # evaluate_open_positions pool so they don't impact paper_balance.
                    # For this step, we just route them through to log execution and append a tag.
                    if channel == "shadow_orders":
                        data["strategy_name"] = f"[SHADOW] {data.get('strategy_name', 'Unknown')}"
                    await execute_market_order(data, redis_client)
                except Exception as e:
                    logger.error(f"Failed to open position: {e}")

            elif channel.startswith("ticks:"):
                # Real-time evaluation of open positions against the live tick
                if data.get("type") == "trade" and "price" in data:
                    try:
                        await evaluate_open_positions(data, redis_client)
                    except Exception as e:
                        logger.error(f"Failed to evaluate position: {e}")

if __name__ == "__main__":
    asyncio.run(main())
