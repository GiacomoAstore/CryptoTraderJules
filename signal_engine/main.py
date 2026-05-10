import asyncio
import json
import logging
import os
import redis.asyncio as redis
from strategy import EMAStrategy, OrderBookImbalanceStrategy, MomentumBurstStrategy
from models import NormalizedTick, MarketContext
from dataclasses import asdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SignalEngine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    # Subscribe to all tick channels
    await pubsub.psubscribe("ticks:*")

    strategies = [EMAStrategy(), OrderBookImbalanceStrategy(), MomentumBurstStrategy()]
    context = MarketContext(price_history={})

    logger.info("Signal Engine started. Waiting for ticks...")

    async for message in pubsub.listen():
        if message["type"] == "pmessage":
            data = json.loads(message["data"])

            # Map raw dictionary to Typed Dataclass
            tick = NormalizedTick(
                symbol=data.get("symbol", ""),
                timestamp_ms=data.get("timestamp_ms", 0),
                type=data.get("type", ""),
                price=data.get("price"),
                qty=data.get("qty"),
                side=data.get("side"),
                bid_price=data.get("bid_price"),
                bid_qty=data.get("bid_qty"),
                ask_price=data.get("ask_price"),
                ask_qty=data.get("ask_qty")
            )

            # Update market context history if it's a trade tick
            if tick.type == "trade" and tick.price:
                if tick.symbol not in context.price_history:
                    context.price_history[tick.symbol] = []
                context.price_history[tick.symbol].append(tick.price)
                if len(context.price_history[tick.symbol]) > 20:
                    context.price_history[tick.symbol].pop(0)

            # Process all strategies
            for strategy in strategies:
                signal = strategy.generate_signal(tick, context)
                if signal:
                    logger.info(f"Signal generated: {signal}")
                    await redis_client.publish("signals", json.dumps(asdict(signal)))

if __name__ == "__main__":
    asyncio.run(main())
