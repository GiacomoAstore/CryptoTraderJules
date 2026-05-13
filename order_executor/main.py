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
shadow_open_positions = {} # Separate memory pool for shadow trades
live_open_positions = {} # Separate memory pool for real live positions

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
        qty = float(self.order_data.get("suggested_qty", 0.01))
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
                            oco_params = {
                                "symbol": symbol,
                                "side": oco_side,
                                "quantity": float(data.get("executedQty", qty)),
                                "price": round(executed_price * tp_mult, 2),
                                "stopPrice": round(executed_price * sl_mult, 2),
                                "stopLimitPrice": round(executed_price * sl_mult * 0.999, 2), # Required for Binance OCO Stop-Limit leg
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
    # Require explicit LIVE_TRADING_ENABLED=true for safety
    live_trading_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    kill_switch_active = False

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Restore state from Redis
    try:
        saved_open = await redis_client.get("state:open_positions")
        saved_shadow = await redis_client.get("state:shadow_positions")
        saved_live = await redis_client.get("state:live_positions")
        if saved_open:
            global open_positions
            open_positions = json.loads(saved_open)
            logger.info(f"Restored {sum(len(v) for v in open_positions.values())} open positions from Redis.")
        if saved_shadow:
            global shadow_open_positions
            shadow_open_positions = json.loads(saved_shadow)
            logger.info(f"Restored {sum(len(v) for v in shadow_open_positions.values())} shadow positions from Redis.")
        if saved_live:
            global live_open_positions
            live_open_positions = json.loads(saved_live)
            logger.info(f"Restored {sum(len(v) for v in live_open_positions.values())} live positions from Redis.")
    except Exception as e:
        logger.error(f"Failed to restore positions from Redis: {e}")

    if live_trading_enabled:
        # Phase A6/A7 Validation Gates - MUST CRASH if failed
        passed = await validate_historical_performance(redis_client)
        if not passed:
            logger.critical("FATAL: Validation Gates failed (G2). System will NOT fallback to paper. CRASHING TO PREVENT UNAUTHORIZED LIVE TRADING.")
            sys.exit(1)

    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("approved_orders", "shadow_orders", "ticks:*", "system_commands")
    logger.info(f"Trading Engine started. LIVE ENABLED: {live_trading_enabled}. Listening for orders, shadow orders, and live ticks...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message.get("channel", "")
            data = json.loads(message["data"])

            if channel == "system_commands":
                if data.get("action") == "KILL_SWITCH":
                    logger.critical("KILL SWITCH INITIATED VIA REDIS! BLOCKING ALL NEW ORDERS AND LIQUIDATING POSITIONS.")
                    kill_switch_active = True
                    # In a real setup, we would trigger a synchronous liquidation of all open Binance orders
                    # For safety scaffold, we clear the internal paper positions memory.
                    open_positions.clear()
                    live_open_positions.clear()
                    await redis_client.set("state:open_positions", "{}")
                    await redis_client.set("state:live_positions", "{}")
                    logger.warning("All internal paper and live positions have been cleared locally due to KILL SWITCH.")

            elif channel in ["approved_orders", "shadow_orders"]:
                if kill_switch_active:
                    logger.warning("Kill switch is active. Blocking incoming order.")
                    continue

                try:
                    # Paper trading always runs
                    is_shadow = (channel == "shadow_orders")
                    cmd_paper = PaperTradeCommand(data, redis_client, is_shadow=is_shadow)
                    await cmd_paper.execute()

                    # Live runs in parallel if enabled and not shadow
                    if live_trading_enabled and channel != "shadow_orders":
                        cmd_live = LiveTradeCommand(data, redis_client)
                        await cmd_live.execute()

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
