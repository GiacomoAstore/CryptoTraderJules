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

import math
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LiveOrderExecutor")

live_open_positions = {} # Separate memory pool for real live positions
exchange_info_cache = {} # Cache for symbol precisions (tickSize, stepSize)

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

def format_precision(value: float, step_size: str) -> float:
    # Converts a value to the correct step_size format specified by Binance
    precision = 0
    if "." in step_size:
        precision = len(step_size.split(".")[1].rstrip("0"))
    return round(value, precision)

class LiveTradeCommand(Command):
    def __init__(self, order_data: dict, redis_client):
        self.order_data = order_data
        self.redis_client = redis_client
        self.result = None
        self.api_key = self._load_secret("binance_api_key", "BINANCE_API_KEY")
        self.secret_key = self._load_secret("binance_api_secret", "BINANCE_API_SECRET")

    def _load_secret(self, secret_name: str, env_fallback: str) -> str:
        # Load from Docker Secrets first, fallback to environment variable
        secret_path = f"/run/secrets/{secret_name}"
        if os.path.exists(secret_path):
            with open(secret_path, "r") as f:
                return f.read().strip()
        return os.getenv(env_fallback, "")

    async def execute(self):
        logger.info(f"Executing Live Trade Entry: {self.order_data}")

        symbol = self.order_data.get("symbol", "").upper()

        # Pull precision metadata
        symbol_info = exchange_info_cache.get(symbol, {})
        tick_size = symbol_info.get("tickSize", "0.01")
        step_size = symbol_info.get("stepSize", "0.001")

        raw_qty = float(self.order_data.get("suggested_qty", 0.01))
        qty = format_precision(raw_qty, step_size)
        side = self.order_data.get("direction", "BUY").upper()

        if not self.api_key or not self.secret_key:
            logger.error("Missing Binance API keys. Cannot execute live trade.")
            return

        base_url = "https://api.binance.com"

        # 20% Hard Cap constraint enforcement
        try:
            account_endpoint = "/api/v3/account"
            ts = int(time.time() * 1000)
            qs = f"timestamp={ts}"
            sig = hmac.new(self.secret_key.encode('utf-8'), qs.encode('utf-8'), hashlib.sha256).hexdigest()
            acc_url = f"{base_url}{account_endpoint}?{qs}&signature={sig}"

            async with aiohttp.ClientSession() as session:
                async with session.get(acc_url, headers={"X-MBX-APIKEY": self.api_key}) as response:
                    if response.status == 200:
                        acc_data = await response.json()
                        usdt_balance = 0.0
                        for asset in acc_data.get("balances", []):
                            if asset["asset"] == "USDT":
                                usdt_balance = float(asset["free"])
                                break

                        # Fetch current price roughly
                        last_tick_raw = await self.redis_client.get(f"tick:last:{symbol}")
                        exec_price = float(self.order_data.get("suggested_price", 0))
                        if last_tick_raw:
                            try:
                                last_tick = json.loads(last_tick_raw)
                                exec_price = float(last_tick.get("price", exec_price))
                            except Exception:
                                pass

                        order_value = exec_price * qty
                        max_allowed_value = usdt_balance * 0.20

                        if order_value > max_allowed_value:
                            logger.critical(f"HARD CAP EXCEEDED: Order value (${order_value:.2f}) is > 20% of USDT balance (${usdt_balance:.2f}). Aborting live trade.")
                            return
        except Exception as e:
            logger.error(f"Failed to fetch account balance for Hard Cap check. Aborting live trade. {e}")
            return

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


                            # OCO Order Logic (Stop Loss / Take Profit)
                            executed_price = float(data.get("cummulativeQuoteQty", 0)) / float(data.get("executedQty", 1)) if float(data.get("executedQty", 0)) > 0 else exec_price

                            sl_mult = 0.995 if side == "BUY" else 1.005
                            tp_mult = 1.005 if side == "BUY" else 0.995

                            oco_side = "SELL" if side == "BUY" else "BUY"
                            oco_endpoint = "/api/v3/order/oco"

                            # Calculate dynamic precisions based on Binance requirements
                            oco_qty = format_precision(float(data.get("executedQty", qty)), step_size)
                            tp_price = format_precision(executed_price * tp_mult, tick_size)
                            sl_price = format_precision(executed_price * sl_mult, tick_size)
                            sl_limit_price = format_precision(executed_price * sl_mult * 0.999, tick_size)

                            oco_params = {
                                "symbol": symbol,
                                "side": oco_side,
                                "quantity": oco_qty,
                                "price": tp_price,
                                "stopPrice": sl_price,
                                "stopLimitPrice": sl_limit_price,
                                "stopLimitTimeInForce": "GTC",
                                "timestamp": int(time.time() * 1000)
                            }
                            oco_qs = urllib.parse.urlencode(oco_params)
                            oco_sig = hmac.new(self.secret_key.encode('utf-8'), oco_qs.encode('utf-8'), hashlib.sha256).hexdigest()
                            oco_url = f"{base_url}{oco_endpoint}?{oco_qs}&signature={oco_sig}"

                            async with session.post(oco_url, headers=headers) as oco_response:
                                oco_data = await oco_response.json()
                                if oco_response.status == 200:
                                    logger.info(f"OCO Placed Successfully: SL/TP active on Binance: {oco_data}")
                                else:
                                    logger.error(f"Failed to place OCO! Risk unprotected: {oco_data}")

                            # Track the Live position locally in memory for state consistency/kill-switch
                            position_id = str(data.get("orderId", uuid.uuid4()))
                            live_pos = {
                                "id": position_id,
                                "symbol": symbol,
                                "side": side,
                                "qty": float(data.get("executedQty", qty)),
                                "entry_price": executed_price,
                                "entry_time": timestamp,
                                "strategy": self.order_data.get("strategy_name", "Unknown"),
                                "oco_order_list_id": oco_data.get("orderListId") if 'oco_data' in locals() and isinstance(oco_data, dict) else None
                            }
                            if symbol not in live_open_positions:
                                live_open_positions[symbol] = []
                            live_open_positions[symbol].append(live_pos)

                            # Persist live position state
                            await self.redis_client.set("state:live_positions", json.dumps(live_open_positions))

                            # Publish to executed_trades so it gets saved to DB
                            db_payload = {
                                "status": "FILLED",
                                "order": {
                                    "symbol": symbol,
                                    "type": side,
                                    "price": executed_price,
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


async def fetch_exchange_info():
    logger.info("Fetching Exchange Info from Binance for precision mapping...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/exchangeInfo") as response:
                if response.status == 200:
                    data = await response.json()
                    for sym in data.get("symbols", []):
                        tick_size = "0.01"
                        step_size = "0.001"
                        for filter in sym.get("filters", []):
                            if filter.get("filterType") == "PRICE_FILTER":
                                tick_size = filter.get("tickSize")
                            elif filter.get("filterType") == "LOT_SIZE":
                                step_size = filter.get("stepSize")
                        exchange_info_cache[sym["symbol"]] = {
                            "tickSize": tick_size,
                            "stepSize": step_size
                        }
                    logger.info("Successfully mapped exchange precision filters.")
                else:
                    logger.error(f"Failed to fetch exchangeInfo: HTTP {response.status}")
    except Exception as e:
        logger.error(f"Error fetching exchangeInfo: {e}")

async def main():
    # Require explicit LIVE_TRADING_ENABLED=true for safety
    live_trading_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    if not live_trading_enabled:
        logger.info("LIVE_TRADING_ENABLED is false. Live Executor is idling.")
        while True:
            await asyncio.sleep(3600)

    await fetch_exchange_info()

    kill_switch_active = False

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Restore state from Redis
    try:
        saved_live = await redis_client.get("state:live_positions")
        if saved_live:
            global live_open_positions
            live_open_positions = json.loads(saved_live)
            logger.info(f"Restored {sum(len(v) for v in live_open_positions.values())} live positions from Redis.")
    except Exception as e:
        logger.error(f"Failed to restore positions from Redis: {e}")

    # Phase A6/A7 Validation Gates - MUST CRASH if failed
    passed = await validate_historical_performance(redis_client)
    if not passed:
        logger.critical("FATAL: Validation Gates failed (G2). System will NOT fallback to paper. CRASHING TO PREVENT UNAUTHORIZED LIVE TRADING.")
        sys.exit(1)

    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("approved_orders", "system_commands")
    logger.info(f"LIVE Trading Engine started. Listening for approved orders...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message.get("channel", "")
            data = json.loads(message["data"])

            if channel == "system_commands":
                if data.get("action") == "KILL_SWITCH":
                    logger.critical("KILL SWITCH INITIATED VIA REDIS! BLOCKING ALL NEW LIVE ORDERS AND LIQUIDATING POSITIONS.")
                    kill_switch_active = True

                    # Real Kill Switch Logic implementation
                    api_key = ""
                    secret_key = ""
                    # Load secrets
                    if os.path.exists("/run/secrets/binance_api_key"):
                        with open("/run/secrets/binance_api_key", "r") as f:
                            api_key = f.read().strip()
                    else:
                        api_key = os.getenv("BINANCE_API_KEY", "")

                    if os.path.exists("/run/secrets/binance_api_secret"):
                        with open("/run/secrets/binance_api_secret", "r") as f:
                            secret_key = f.read().strip()
                    else:
                        secret_key = os.getenv("BINANCE_API_SECRET", "")

                    if not api_key or not secret_key:
                         logger.error("KILL SWITCH FAILED TO LOAD API KEYS. Cannot liquidate.")
                         continue

                    base_url = "https://api.binance.com"
                    headers = {"X-MBX-APIKEY": api_key}

                    try:
                        async with aiohttp.ClientSession() as session:
                            for symbol, positions in list(live_open_positions.items()):
                                for pos in positions:
                                    # 1. Cancel existing OCO orders if possible
                                    if pos.get("oco_order_list_id"):
                                        cancel_params = {
                                            "symbol": symbol,
                                            "orderListId": pos["oco_order_list_id"],
                                            "timestamp": int(time.time() * 1000)
                                        }
                                        qs = urllib.parse.urlencode(cancel_params)
                                        sig = hmac.new(secret_key.encode('utf-8'), qs.encode('utf-8'), hashlib.sha256).hexdigest()
                                        cancel_url = f"{base_url}/api/v3/orderList?{qs}&signature={sig}"
                                        async with session.delete(cancel_url, headers=headers) as resp:
                                            logger.info(f"KILL SWITCH: Cancelled OCO for {symbol}: HTTP {resp.status}")

                                    # 2. Liquidate with MARKET order
                                    liq_side = "SELL" if pos["side"] == "BUY" else "BUY"

                                    # Pull precision
                                    sym_info = exchange_info_cache.get(symbol, {})
                                    step_size = sym_info.get("stepSize", "0.001")
                                    liq_qty = format_precision(pos["qty"], step_size)

                                    liq_params = {
                                        "symbol": symbol,
                                        "side": liq_side,
                                        "type": "MARKET",
                                        "quantity": liq_qty,
                                        "timestamp": int(time.time() * 1000)
                                    }
                                    qs2 = urllib.parse.urlencode(liq_params)
                                    sig2 = hmac.new(secret_key.encode('utf-8'), qs2.encode('utf-8'), hashlib.sha256).hexdigest()
                                    liq_url = f"{base_url}/api/v3/order?{qs2}&signature={sig2}"

                                    async with session.post(liq_url, headers=headers) as resp:
                                        logger.info(f"KILL SWITCH: Liquidated {liq_qty} of {symbol} at Market: HTTP {resp.status}")
                    except Exception as e:
                        logger.error(f"Error during KILL SWITCH liquidation: {e}")

                    live_open_positions.clear()
                    await redis_client.set("state:live_positions", "{}")
                    logger.warning("Kill Switch logic completed. Internal memory cleared.")

            elif channel == "approved_orders":
                if kill_switch_active:
                    logger.warning("Kill switch is active. Blocking incoming live order.")
                    continue

                try:
                    cmd_live = LiveTradeCommand(data, redis_client)
                    await cmd_live.execute()
                except Exception as e:
                    logger.error(f"Failed to open live position: {e}")

            elif channel.startswith("ticks:"):
                # Basic fallback check: if we somehow miss Binance Webhook/User stream updates,
                # we theoretically need a loop here to poll for closed orders or trigger timeouts.
                # For this step, the requirement specifies ensuring the system closes positions based on live ticks.
                pass

if __name__ == "__main__":
    asyncio.run(main())
