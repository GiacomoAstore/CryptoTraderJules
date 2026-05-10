import asyncio
import json
import logging
import os
import time
import uuid
import random
from typing import Dict, Any
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
MAX_TRADE_DURATION_SECONDS = int(os.getenv("MAX_TRADE_DURATION_SECONDS", 300))
STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", 100.0))

if not PAPER_TRADING:
    logger.critical("REAL TRADING NOT IMPLEMENTED — set PAPER_TRADING=true")
    # We will enforce paper trading by raising an exception if someone tries to run it live
    raise Exception("REAL TRADING NOT IMPLEMENTED — set PAPER_TRADING=true")

class OrderCommand:
    def __init__(self, data: dict):
        self.command_id = data.get("command_id", str(uuid.uuid4()))
        self.symbol = data["symbol"]
        self.direction = data["type"] # BUY or SELL
        self.target_price = data["price"]
        self.quantity = data["quantity"]
        self.stop_loss = data.get("stop_loss_price")
        self.take_profit = data.get("take_profit_price")
        self.strategy = data.get("strategy", "Unknown")
        self.created_at = time.time()
        self.status = "PENDING"
        self.executed_price = 0.0
        
    def to_dict(self):
        return {
            "command_id": self.command_id,
            "symbol": self.symbol,
            "type": self.direction,
            "price": self.target_price,
            "quantity": self.quantity,
            "stop_loss_price": self.stop_loss,
            "take_profit_price": self.take_profit,
            "strategy": self.strategy,
            "status": self.status,
            "executed_price": self.executed_price,
            "created_at": self.created_at
        }

class PaperEngine:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.active_orders: Dict[str, OrderCommand] = {} # Pending limit orders
        self.open_positions: Dict[str, OrderCommand] = {} # Filled positions monitoring SL/TP
        self.paper_balance = STARTING_CAPITAL
        self.processed_commands = set()

    async def init_ledger(self):
        balance = await self.redis_client.get("paper:balance")
        if balance:
            self.paper_balance = float(balance)
        else:
            await self.redis_client.set("paper:balance", self.paper_balance)

    async def send_telegram_alert(self, message: str):
        payload = {"event": "trade", "message": message}
        await self.redis_client.publish("alerts:telegram", json.dumps(payload))

    async def log_to_timescaledb(self, table: str, payload: dict):
        # We publish to a channel that the API gateway (or dedicated DB writer) listens to
        if table == "trades":
            await self.redis_client.publish("executed_trades", json.dumps(payload))

    async def process_new_command(self, cmd_data: dict):
        cmd = OrderCommand(cmd_data)
        if cmd.command_id in self.processed_commands:
            return
            
        self.processed_commands.add(cmd.command_id)
        
        # Determine if we can execute immediately (Market) or must wait (Limit)
        # For simplicity, we assume market execution with latency
        await asyncio.sleep(random.uniform(0.05, 0.15)) # 50-150ms simulated latency
        
        # Get latest tick
        tick_str = await self.redis_client.get(f"tick:last:{cmd.symbol.upper()}")
        if tick_str:
            tick = json.loads(tick_str)
            execute_price = float(tick["price"])
        else:
            execute_price = cmd.target_price

        # Execute
        cmd.status = "FILLED"
        cmd.executed_price = execute_price
        
        logger.info(f"PAPER EXECUTION: {cmd.direction} {cmd.quantity:.4f} {cmd.symbol} @ {cmd.executed_price:.2f} (SL: {cmd.stop_loss}, TP: {cmd.take_profit})")
        await self.send_telegram_alert(f"🟢 TRADE OPENED\n{cmd.direction} {cmd.symbol}\nQty: {cmd.quantity:.4f}\nPrice: {cmd.executed_price:.2f}")

        self.open_positions[cmd.symbol] = cmd

    async def close_position(self, symbol: str, close_price: float, reason: str):
        if symbol not in self.open_positions:
            return
            
        pos = self.open_positions[symbol]
        
        # Calculate PNL
        if pos.direction == "BUY":
            pnl_usdt = (close_price - pos.executed_price) * pos.quantity
        else:
            pnl_usdt = (pos.executed_price - close_price) * pos.quantity
            
        pnl_pct = (pnl_usdt / (pos.executed_price * pos.quantity)) * 100
        
        # Apply 0.1% fee simulation for entry and exit
        fee = (pos.executed_price * pos.quantity * 0.001) + (close_price * pos.quantity * 0.001)
        net_pnl = pnl_usdt - fee

        # Update paper balance
        self.paper_balance += net_pnl
        await self.redis_client.set("paper:balance", self.paper_balance)

        # Log trade
        trade_record = {
            "id": str(uuid.uuid4()),
            "symbol": pos.symbol,
            "side": pos.direction,
            "entry_price": pos.executed_price,
            "exit_price": close_price,
            "quantity": pos.quantity,
            "pnl_usdt": net_pnl,
            "pnl_pct": pnl_pct,
            "open_time": int(pos.created_at * 1000),
            "close_time": int(time.time() * 1000),
            "strategy_name": pos.strategy,
            "stop_loss_price": pos.stop_loss,
            "take_profit_price": pos.take_profit,
            "close_reason": reason
        }
        
        logger.info(f"POSITION CLOSED: {pos.symbol} @ {close_price:.2f} | PNL: {net_pnl:.2f} USDT | Reason: {reason}")
        
        await self.log_to_timescaledb("trades", trade_record)
        del self.open_positions[symbol]

    async def monitor_ticks(self, tick: dict):
        symbol = tick["symbol"]
        price = float(tick["price"])
        
        if symbol not in self.open_positions:
            return
            
        pos = self.open_positions[symbol]
        
        # Check SL
        if pos.stop_loss:
            if (pos.direction == "BUY" and price <= pos.stop_loss) or \
               (pos.direction == "SELL" and price >= pos.stop_loss):
                await self.close_position(symbol, price, "SL_HIT")
                return
                
        # Check TP
        if pos.take_profit:
            if (pos.direction == "BUY" and price >= pos.take_profit) or \
               (pos.direction == "SELL" and price <= pos.take_profit):
                await self.close_position(symbol, price, "TP_HIT")
                return
                
        # Check Timeout
        if time.time() - pos.created_at > MAX_TRADE_DURATION_SECONDS:
            await self.close_position(symbol, price, "TIMEOUT")
            return

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    
    engine = PaperEngine(redis_client)
    await engine.init_ledger()
    
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("approved_orders:*", "ticks:*", "approved_orders") # support legacy channel
    
    logger.info("Paper Trading Engine started. Monitoring...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            data = json.loads(message["data"])

            if channel.startswith("ticks:"):
                await engine.monitor_ticks(data)
            elif "approved_orders" in channel:
                asyncio.create_task(engine.process_new_command(data))

if __name__ == "__main__":
    asyncio.run(main())
