import asyncio
import json
import logging
import os
import sys
import uuid
import time
import aiohttp
import redis.asyncio as redis
from abc import ABC, abstractmethod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PaperOrderExecutor")

COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", 0.001)) # 0.1% defaults
open_positions = {} # symbol -> list of position dicts
shadow_open_positions = {} # Separate memory pool for shadow trades

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class Command(ABC):
    @abstractmethod
    async def execute(self):
        pass

    @abstractmethod
    async def undo(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

class PaperTradeCommand(Command):
    def __init__(self, order_data: dict, redis_client, is_shadow: bool = False):
        self.order_data = order_data
        self.redis_client = redis_client
        self.position = None
        self.is_shadow = is_shadow

    async def execute(self):
        logger.info(f"Executing Paper Trade Entry: {self.order_data}")

        symbol = self.order_data.get("symbol", "")
        last_tick_raw = await self.redis_client.get(f"tick:last:{symbol}")

        exec_price = float(self.order_data.get("suggested_price", 0))
        if last_tick_raw:
            try:
                last_tick = json.loads(last_tick_raw)
                exec_price = float(last_tick.get("price", exec_price))
            except Exception:
                pass

        import random
        await asyncio.sleep(random.uniform(0.05, 0.15))

        if exec_price <= 0:
            logger.error("Invalid execution price. Aborting.")
            return

        qty = float(self.order_data.get("suggested_qty", 0.01))
        side = self.order_data.get("direction", "BUY").upper()

        position_id = str(uuid.uuid4())

        sl_mult = 0.995 if side == "BUY" else 1.005
        tp_mult = 1.005 if side == "BUY" else 0.995

        self.position = {
            "id": position_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": exec_price,
            "entry_time": int(time.time() * 1000),
            "stop_loss": exec_price * sl_mult,
            "take_profit": exec_price * tp_mult,
            "strategy": f"[SHADOW] {self.order_data.get('strategy_name', 'Unknown')}" if self.is_shadow else self.order_data.get("strategy_name", "Unknown")
        }

        target_pool = shadow_open_positions if self.is_shadow else open_positions
        if symbol not in target_pool:
            target_pool[symbol] = []
        target_pool[symbol].append(self.position)

        # Persist to Redis
        pool_key = "state:shadow_positions" if self.is_shadow else "state:open_positions"
        await self.redis_client.set(pool_key, json.dumps(target_pool))

        logger.info(f"{'Shadow ' if self.is_shadow else ''}Position opened via PaperTradeCommand: {self.position}")

    async def undo(self):
        if not self.position:
            return
        symbol = self.position["symbol"]
        target_pool = shadow_open_positions if self.is_shadow else open_positions
        if symbol in target_pool:
            target_pool[symbol] = [p for p in target_pool[symbol] if p["id"] != self.position["id"]]
            logger.info(f"Undo PaperTradeCommand: Position {self.position['id']} removed.")
            self.position = None

    def to_dict(self):
        return {
            "type": "PAPER_TRADE",
            "order_data": self.order_data,
            "status": "EXECUTED" if self.position else "PENDING"
        }

async def evaluate_open_positions(tick: dict, redis_client):
    symbol = tick.get("symbol")
    current_price = float(tick.get("price", 0))
    if current_price <= 0:
        return

    # Evaluate both real paper positions and shadow positions
    await _evaluate_pool(symbol, current_price, open_positions, redis_client, is_shadow=False)
    await _evaluate_pool(symbol, current_price, shadow_open_positions, redis_client, is_shadow=True)

async def _evaluate_pool(symbol: str, current_price: float, pool: dict, redis_client, is_shadow: bool):
    if symbol not in pool or not pool[symbol]:
        return

    remaining_positions = []
    for pos in pool[symbol]:
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

            # Only update the global virtual balance if it's NOT a shadow trade
            if not is_shadow:
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

    pool[symbol] = remaining_positions

    # Persist back to Redis after processing
    pool_key = "state:shadow_positions" if is_shadow else "state:open_positions"
    await redis_client.set(pool_key, json.dumps(pool))

async def validate_historical_performance(redis_client) -> bool:
    logger.info("Validating historical paper trading performance before allowing LIVE execution...")
    # Fetch metrics from API Gateway / Redis
    # Minimum requirements for live trading: Win Rate > 40%, Max Drawdown < -500
    try:
        async with aiohttp.ClientSession() as session:
            # We assume api_gateway is running on the docker network as 'api_gateway'
            gateway_url = os.getenv("API_GATEWAY_URL", "http://api_gateway:8000")
            async with session.get(f"{gateway_url}/api/metrics") as response:
                if response.status == 200:
                    data = await response.json()
                    metrics = data.get("metrics", {})
                    win_rate = metrics.get("win_rate", 0)
                    max_drawdown = metrics.get("max_drawdown", 0)

                    # NOTE: A real system might look at 2 weeks of aggregated data.
                    # For this step, we use the metrics endpoint.
                    logger.info(f"Historical Metrics - Win Rate: {win_rate}%, Max DD: {max_drawdown}")

                    if win_rate < 40.0:
                        logger.critical("Validation Gate Failed: Win Rate below 40%.")
                        return False
                    if max_drawdown < -500.0:
                        logger.critical("Validation Gate Failed: Max Drawdown too severe.")
                        return False

                    logger.info("Validation Gate Passed. System is clear for LIVE trading.")
                    return True
                else:
                    logger.error(f"Failed to fetch metrics from API Gateway: {response.status}")
                    return False
    except Exception as e:
        logger.error(f"Error during validation gate check: {e}")
        return False


async def main():
    kill_switch_active = False

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Restore state from Redis
    try:
        saved_open = await redis_client.get("state:open_positions")
        saved_shadow = await redis_client.get("state:shadow_positions")
        if saved_open:
            global open_positions
            open_positions = json.loads(saved_open)
            logger.info(f"Restored {sum(len(v) for v in open_positions.values())} open positions from Redis.")
        if saved_shadow:
            global shadow_open_positions
            shadow_open_positions = json.loads(saved_shadow)
            logger.info(f"Restored {sum(len(v) for v in shadow_open_positions.values())} shadow positions from Redis.")
    except Exception as e:
        logger.error(f"Failed to restore positions from Redis: {e}")

    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("approved_orders", "shadow_orders", "ticks:*", "system_commands")
    logger.info(f"Paper Trading Engine started. Listening for orders, shadow orders, and live ticks...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message.get("channel", "")
            data = json.loads(message["data"])

            if channel == "system_commands":
                if data.get("action") == "KILL_SWITCH":
                    logger.critical("KILL SWITCH INITIATED VIA REDIS! BLOCKING ALL NEW PAPER ORDERS.")
                    kill_switch_active = True
                    open_positions.clear()
                    await redis_client.set("state:open_positions", "{}")
                    logger.warning("All internal paper positions have been cleared locally due to KILL SWITCH.")

            elif channel in ["approved_orders", "shadow_orders"]:
                if kill_switch_active:
                    logger.warning("Kill switch is active. Blocking incoming order.")
                    continue

                try:
                    is_shadow = (channel == "shadow_orders")
                    cmd_paper = PaperTradeCommand(data, redis_client, is_shadow=is_shadow)
                    await cmd_paper.execute()
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
