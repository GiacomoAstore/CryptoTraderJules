import asyncio
import json
import logging
import os
import redis.asyncio as redis
from strategy import EmaCrossoverStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SignalEngine")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    # Subscribe to all tick channels
    await pubsub.psubscribe("ticks:*")

    strategy = EmaCrossoverStrategy()
    logger.info("Signal Engine started. Waiting for ticks...")

    async for message in pubsub.listen():
        if message["type"] == "pmessage":
            tick = json.loads(message["data"])
            signal = strategy.generate_signal(tick)
            if signal:
                logger.info(f"Signal generated: {signal}")
                await redis_client.publish("signals", json.dumps(signal))

if __name__ == "__main__":
    asyncio.run(main())
