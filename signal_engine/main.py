import asyncio
import json
import logging
import os
import time
from collections import deque, defaultdict
import redis.asyncio as redis
import strategy
from dataclasses import asdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SignalEngine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MIN_CONSENSUS = int(os.getenv("MIN_CONSENSUS", 2))
MIN_SIGNAL_INTERVAL_MS = int(os.getenv("MIN_SIGNAL_INTERVAL_MS", 500))

class SignalEngine:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.strategies = []
        
        # State tracking for MarketContext
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.tick_history = defaultdict(lambda: deque(maxlen=100))
        self.last_signal_time = defaultdict(int)
        
        # Setup strategies
        self.setup_strategies()

    def setup_strategies(self):
        # We will load from env or redis later, for now we instantiate them directly
        self.strategies = [
            strategy.EMAStrategy({"weight": 1.0}),
            strategy.OrderBookImbalanceStrategy({"weight": 1.0}),
            strategy.MomentumBurstStrategy({"weight": 1.0})
        ]
        logger.info(f"Loaded {len(self.strategies)} strategies.")

    async def update_config_from_redis(self):
        while True:
            try:
                config_str = await self.redis_client.get("config:strategies")
                if config_str:
                    config = json.loads(config_str)
                    MIN_CONSENSUS = config.get("min_consensus", 2)
                    # Future dynamic strategy updates
            except Exception as e:
                logger.error(f"Error loading config from Redis: {e}")
            await asyncio.sleep(5)

    async def run(self):
        asyncio.create_task(self.update_config_from_redis())
        
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("ticks:*")
        logger.info("Signal Engine started. Waiting for ticks...")

        async for message in pubsub.listen():
            if message["type"] in ["message", "pmessage"]:
                channel = message.get("channel", "")
                
                if not channel.startswith("ticks:"):
                    continue

                tick = json.loads(message["data"])
                symbol = tick.get("symbol")
                if not symbol:
                    continue

                # Anti-spam check
                current_time_ms = int(time.time() * 1000)
                if current_time_ms - self.last_signal_time[symbol] < MIN_SIGNAL_INTERVAL_MS:
                    continue

                # Update state
                self.price_history[symbol].append(tick["price"])
                self.tick_history[symbol].append(tick)

                context = strategy.MarketContext(
                    price_history=self.price_history[symbol],
                    tick_history=self.tick_history[symbol],
                    current_position=None # Will be updated by Risk Manager feedback in the future
                )
                
                buy_votes = 0.0
                sell_votes = 0.0
                suggested_prices_buy = []
                suggested_prices_sell = []

                for strat in self.strategies:
                    if not strat.enabled: continue
                    
                    signal = strat.generate_signal(tick, context)
                    if signal:
                        weight = strat.weight * signal.strength
                        if signal.direction == "BUY":
                            buy_votes += weight
                            suggested_prices_buy.append(signal.suggested_price)
                        elif signal.direction == "SELL":
                            sell_votes += weight
                            suggested_prices_sell.append(signal.suggested_price)

                final_signal = None
                if buy_votes >= MIN_CONSENSUS and buy_votes > sell_votes:
                    avg_price = sum(suggested_prices_buy) / len(suggested_prices_buy)
                    final_signal = strategy.Signal(
                        symbol=symbol,
                        direction="BUY",
                        strength=min(1.0, buy_votes / len(self.strategies)),
                        strategy_name="Consensus",
                        timestamp_ms=current_time_ms,
                        suggested_price=avg_price
                    )
                elif sell_votes >= MIN_CONSENSUS and sell_votes > buy_votes:
                    avg_price = sum(suggested_prices_sell) / len(suggested_prices_sell)
                    final_signal = strategy.Signal(
                        symbol=symbol,
                        direction="SELL",
                        strength=min(1.0, sell_votes / len(self.strategies)),
                        strategy_name="Consensus",
                        timestamp_ms=current_time_ms,
                        suggested_price=avg_price
                    )

                if final_signal:
                    self.last_signal_time[symbol] = current_time_ms
                    logger.info(f"Consensus Signal: {final_signal.direction} {symbol} (Buy: {buy_votes}, Sell: {sell_votes})")
                    
                    # Ensure compatibility with existing RiskManager format
                    compat_signal = {
                        "type": final_signal.direction,
                        "symbol": final_signal.symbol,
                        "price": final_signal.suggested_price,
                        "strength": final_signal.strength,
                        "timestamp_ms": final_signal.timestamp_ms
                    }
                    await self.redis_client.publish(f"signals:{symbol}", json.dumps(compat_signal))
                    # Also publish to old channel for legacy compatibility during transition
                    await self.redis_client.publish("signals", json.dumps(compat_signal))

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    engine = SignalEngine(redis_client)
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())
