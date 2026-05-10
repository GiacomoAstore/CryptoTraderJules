import asyncio
import json
import logging
import os
import sys
import redis.asyncio as redis
from abc import ABC, abstractmethod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class Command(ABC):
    @abstractmethod
    async def execute(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

class ExecuteOrderCommand(Command):
    def __init__(self, order_data: dict, redis_client):
        self.order_data = order_data
        self.redis = redis_client
        self.COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", 0.001)) # 0.1% defaults

    async def execute(self):
        logger.info(f"Simulating Paper Trade execution: {self.order_data}")

        symbol = self.order_data.get("symbol", "")
        # Try to fetch current real market price from Redis tick cache
        last_tick_raw = await self.redis.get(f"tick:last:{symbol}")

        # Determine execution price (using suggested price if tick cache fails)
        exec_price = self.order_data.get("suggested_price", 0)
        if last_tick_raw:
            try:
                last_tick = json.loads(last_tick_raw)
                exec_price = float(last_tick.get("price", exec_price))
            except Exception:
                pass

        # Simulate network latency (50ms - 150ms)
        import random
        await asyncio.sleep(random.uniform(0.05, 0.15))

        qty = self.order_data.get("suggested_qty", 0.01)
        gross_value = exec_price * qty
        commission = gross_value * self.COMMISSION_RATE

        # Very simple mock PnL for demonstration (real system would wait for close)
        # We will assume a random walk PnL simulation for a quick trade just to populate data
        simulated_pnl = gross_value * random.uniform(-0.01, 0.015)
        net_pnl = simulated_pnl - commission

        execution_result = {
            "status": "FILLED",
            "order": {
                "symbol": symbol,
                "type": self.order_data.get("direction", "BUY"),
                "price": exec_price,
                "quantity": qty,
                "strategy": self.order_data.get("strategy_name", "Unknown")
            },
            "commission_paid": commission,
            "pnl_netto": net_pnl
        }

        return execution_result

    def to_dict(self):
        return self.order_data

async def main():
    # PAPER_TRADING safety guard constraint
    paper_trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
    if paper_trading_mode != "PAPER":
        logger.critical("REAL TRADING NOT IMPLEMENTED — set TRADING_MODE=PAPER (or PAPER_TRADING=true)")
        sys.exit(1)

    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.subscribe("approved_orders")
    logger.info("Order Executor started. Listening for approved orders...")

    async for message in pubsub.listen():
        if message["type"] == "message":
            order_data = json.loads(message["data"])
            command = ExecuteOrderCommand(order_data, redis_client)
            try:
                result = await command.execute()
                logger.info(f"Order filled: {result}")
                # Publish execution result for timescaledb/dashboard
                await redis_client.publish("executed_trades", json.dumps(result))
            except Exception as e:
                logger.error(f"Failed to execute order: {e}")

if __name__ == "__main__":
    asyncio.run(main())
