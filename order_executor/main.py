import asyncio
import json
import logging
import os
import sys
import uuid
import time
import hmac
import hashlib
import urllib.parse
import aiohttp
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
    async def undo(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

class LiveTradeCommand(Command):
    def __init__(self, order_data: dict, redis_client):
        self.order_data = order_data
        self.redis_client = redis_client
        self.result = None
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.secret_key = os.getenv("BINANCE_SECRET_KEY", "")

    async def execute(self):
        logger.info(f"Executing Live Trade Entry: {self.order_data}")

        symbol = self.order_data.get("symbol", "").upper()
        qty = float(self.order_data.get("suggested_qty", 0.01))
        side = self.order_data.get("direction", "BUY").upper()

        if not self.api_key or not self.secret_key:
            logger.error("Missing Binance API keys. Cannot execute live trade.")
            return

        base_url = "https://api.binance.com"
        endpoint = "/api/v3/order"

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "timestamp": timestamp
        }

        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(self.secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

        url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key}

        # Exponential backoff retry logic for Binance API
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers) as response:
                        data = await response.json()
                        if response.status == 200:
                            logger.info(f"Live trade executed successfully: {data}")
                            self.result = data

                            # Publish to executed_trades so it gets saved to DB
                            db_payload = {
                                "status": "FILLED",
                                "order": {
                                    "symbol": symbol,
                                    "type": side,
                                    "price": float(data.get("cummulativeQuoteQty", 0)) / float(data.get("executedQty", 1)) if float(data.get("executedQty", 0)) > 0 else 0,
                                    "quantity": float(data.get("executedQty", qty)),
                                    "strategy": self.order_data.get("strategy_name", "Unknown")
                                },
                                "close_reason": "LIVE_MARKET",
                                "gross_pnl": 0.0,
                                "commission_paid": 0.0,
                                "pnl_netto": 0.0
                            }
                            await self.redis_client.publish("executed_trades", json.dumps(db_payload))
                            return
                        else:
                            logger.error(f"Binance API error (Attempt {attempt+1}): {data}")
                            if response.status in [429, 418]: # Rate limit
                                await asyncio.sleep((2 ** attempt))
                                continue
                            else:
                                break
            except Exception as e:
                logger.error(f"Network error during live trade (Attempt {attempt+1}): {e}")
                await asyncio.sleep((2 ** attempt))

        logger.error("Live trade execution failed after retries.")

    async def undo(self):
        # In a real environment, undoing a market order means submitting an opposite order
        # For safety in this scaffold, we simply log it.
        logger.warning(f"Cannot reliably undo a live market order {self.result}. Manual intervention required.")

    def to_dict(self):
        return {
            "type": "LIVE_TRADE",
            "order_data": self.order_data,
            "status": "EXECUTED" if self.result else "FAILED"
        }

class PaperTradeCommand(Command):
    def __init__(self, order_data: dict, redis_client):
        self.order_data = order_data
        self.redis_client = redis_client
        self.position = None

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
            "strategy": self.order_data.get("strategy_name", "Unknown")
        }

        if symbol not in open_positions:
            open_positions[symbol] = []
        open_positions[symbol].append(self.position)
        logger.info(f"Position opened via PaperTradeCommand: {self.position}")

    async def undo(self):
        if not self.position:
            return
        symbol = self.position["symbol"]
        if symbol in open_positions:
            open_positions[symbol] = [p for p in open_positions[symbol] if p["id"] != self.position["id"]]
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
    trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    if trading_mode == "LIVE":
        # Phase A6/A7 Validation Gates
        passed = await validate_historical_performance(redis_client)
        if not passed:
            logger.critical("Validation Gates failed. Falling back to PAPER trading mode.")
            trading_mode = "PAPER"

    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("approved_orders", "shadow_orders", "ticks:*")
    logger.info(f"{trading_mode} Trading Engine started. Listening for orders, shadow orders, and live ticks...")

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

                    if trading_mode == "PAPER":
                        cmd = PaperTradeCommand(data, redis_client)
                        await cmd.execute()
                    elif trading_mode == "LIVE":
                        cmd = LiveTradeCommand(data, redis_client)
                        await cmd.execute()
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
