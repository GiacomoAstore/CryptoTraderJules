import asyncio
import json
import logging
import os
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskManager")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class RiskManager:
    def __init__(self):
        self.circuit_breaker_open = False

    def evaluate_signal(self, signal: dict) -> bool:
        if self.circuit_breaker_open:
            logger.warning("Circuit breaker is open. Rejecting signal.")
            return False

        # Basic check
        if signal.get("price", 0) <= 0:
            return False

        return True

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.subscribe("signals")

    risk_manager = RiskManager()
    logger.info("Risk Manager started. Listening for signals...")

    async for message in pubsub.listen():
        if message["type"] == "message":
            signal = json.loads(message["data"])
            if risk_manager.evaluate_signal(signal):
                logger.info(f"Signal approved by Risk Manager: {signal}")
                await redis_client.publish("approved_orders", json.dumps(signal))
            else:
                logger.info(f"Signal rejected by Risk Manager: {signal}")

if __name__ == "__main__":
    asyncio.run(main())
