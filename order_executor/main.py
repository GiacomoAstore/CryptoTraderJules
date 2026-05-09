import asyncio
import json
import logging
import os
import redis.asyncio as redis
from abc import ABC, abstractmethod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class Command(ABC):
    @abstractmethod
    async def execute(self):
        pass

    @abstractmethod
    def to_dict(self):
        pass

class ExecuteOrderCommand(Command):
    def __init__(self, order_data: dict):
        self.order_data = order_data

    async def execute(self):
        # Mocking Binance API execution
        logger.info(f"Executing order on Binance: {self.order_data}")
        await asyncio.sleep(0.1) # Simulate network delay
        # In real life, handle retries, idempotency, etc.
        return {"status": "FILLED", "order": self.order_data}

    def to_dict(self):
        return self.order_data

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.subscribe("approved_orders")
    logger.info("Order Executor started. Listening for approved orders...")

    async for message in pubsub.listen():
        if message["type"] == "message":
            order_data = json.loads(message["data"])
            command = ExecuteOrderCommand(order_data)
            try:
                result = await command.execute()
                logger.info(f"Order filled: {result}")
                # Publish execution result for timescaledb/dashboard
                await redis_client.publish("executed_trades", json.dumps(result))
            except Exception as e:
                logger.error(f"Failed to execute order: {e}")

if __name__ == "__main__":
    asyncio.run(main())
