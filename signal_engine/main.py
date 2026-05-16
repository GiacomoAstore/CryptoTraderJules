import asyncio
import json
import logging
import os
import time
import yaml
from collections import deque, defaultdict
from decimal import Decimal, getcontext
import redis.asyncio as redis
import strategy
from dataclasses import asdict

getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SignalEngine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MIN_CONSENSUS = Decimal(os.getenv("MIN_CONSENSUS", "2"))
MIN_SIGNAL_INTERVAL_MS = int(os.getenv("MIN_SIGNAL_INTERVAL_MS", 500))

class SignalEngine:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.strategies = []
        
        # State tracking for MarketContext
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.tick_history = defaultdict(lambda: deque(maxlen=100))
        self.last_signal_time = defaultdict(int)
        
        self.setup_strategies()

    def setup_strategies(self):
        config_path = "/app/shared_config/config.yaml"
        self.strategies = []
        global MIN_CONSENSUS

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    cfg = yaml.safe_load(f)
                    
                    if cfg and "min_consensus" in cfg:
                        MIN_CONSENSUS = Decimal(str(cfg["min_consensus"]))
                    elif cfg and "consensus" in cfg and "threshold" in cfg["consensus"]:
                        MIN_CONSENSUS = Decimal(str(cfg["consensus"]["threshold"]))
                        
                    if cfg and "strategies" in cfg:
                        for s in cfg["strategies"]:
                            name = s["name"]
                            # Mappatura nomi corretta se necessario (es. EmaCrossoverStrategy -> EMAStrategy)
                            if name == "EmaCrossoverStrategy":
                                name = "EMAStrategy"
                                
                            if not s.get("enabled", True):
                                continue
                                
                            weight = Decimal(str(s.get("weight", "1.0")))
                            
                            # Supporto diversi formati config
                            for variant_key, variant_name in [("params", "A"), ("variant_a", "A"), ("variant_b", "B")]:
                                if variant_key in s:
                                    params = s[variant_key].copy()
                                    params["weight"] = weight
                                    params["ab_variant"] = variant_name
                                    strat_class = getattr(strategy, name, None)
                                    if strat_class:
                                        self.strategies.append(strat_class(params))
                                        logger.info(f"Loaded strategy: {name} (Variant {variant_name})")
                                    else:
                                        logger.warning(f"Strategy class not found: {name}")
                                        
            except Exception as e:
                logger.error(f"Failed to load config.yaml: {e}")
                
        if not self.strategies:
            self.strategies = [
                strategy.EMAStrategy({"weight": "1.0", "ab_variant": "A"}),
                strategy.OrderBookImbalanceStrategy({"weight": "1.0", "ab_variant": "A"}),
                strategy.MomentumBurstStrategy({"weight": "1.0", "ab_variant": "A"})
            ]

        logger.info(f"Loaded {len(self.strategies)} strategy instances. Consensus required: {MIN_CONSENSUS}")

    async def run(self):
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("ticks:*", "system:commands")
        logger.info("Signal Engine started. Waiting for ticks...")

        async for message in pubsub.listen():
            if message["type"] in ["message", "pmessage"]:
                channel = message.get("channel", "")
                
                if channel == "system:commands":
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode('utf-8')
                    if data == "RELOAD_CONFIG":
                        logger.info("Received RELOAD_CONFIG command. Reloading strategies...")
                        self.setup_strategies()
                    continue

                if not channel.startswith("ticks:"):
                    continue

                try:
                    tick_raw = json.loads(message["data"])

                    symbol = tick_raw.get("symbol")
                    if not symbol: continue

                    # Normalizzazione tick con Decimal
                    tick: strategy.NormalizedTick = {
                        "symbol": symbol,
                        "price": Decimal(str(tick_raw["price"])),
                        "qty": Decimal(str(tick_raw["qty"])),
                        "side": tick_raw["side"],
                        "timestamp_ms": tick_raw["timestamp_ms"],
                        "bid_price": Decimal(str(tick_raw.get("bid_price", 0))),
                        "ask_price": Decimal(str(tick_raw.get("ask_price", 0))),
                        "bid_qty": Decimal(str(tick_raw.get("bid_qty", 0))),
                        "ask_qty": Decimal(str(tick_raw.get("ask_qty", 0)))
                    }

                    current_time_ms = int(time.time() * 1000)
                    if current_time_ms - self.last_signal_time[symbol] < MIN_SIGNAL_INTERVAL_MS:
                        continue

                    self.price_history[symbol].append(tick["price"])
                    self.tick_history[symbol].append(tick)

                    context = strategy.MarketContext(
                        price_history=self.price_history[symbol],
                        tick_history=self.tick_history[symbol],
                        current_position=None
                    )
                    
                    votes_buy = {"A": Decimal("0"), "B": Decimal("0")}
                    votes_sell = {"A": Decimal("0"), "B": Decimal("0")}
                    suggested_prices_buy = {"A": [], "B": []}
                    suggested_prices_sell = {"A": [], "B": []}
                    strats_count = {"A": 0, "B": 0}

                    for strat in self.strategies:
                        if not strat.enabled: continue
                        variant = strat.ab_variant
                        strats_count[variant] += 1
                        
                        signal = strat.generate_signal(tick, context)
                        if signal:
                            weight = strat.weight * signal.strength
                            if signal.direction == "BUY":
                                votes_buy[variant] += weight
                                suggested_prices_buy[variant].append(signal.suggested_price)
                            elif signal.direction == "SELL":
                                votes_sell[variant] += weight
                                suggested_prices_sell[variant].append(signal.suggested_price)

                    for variant in ["A", "B"]:
                        if strats_count[variant] == 0: continue
                        
                        v_buy = votes_buy[variant]
                        v_sell = votes_sell[variant]
                        
                        final_signal = None
                        if v_buy >= MIN_CONSENSUS and v_buy > v_sell and suggested_prices_buy[variant]:
                            avg_price = sum(suggested_prices_buy[variant]) / Decimal(str(len(suggested_prices_buy[variant])))
                            final_signal = {
                                "type": "BUY",
                                "symbol": symbol,
                                "price": str(avg_price),
                                "strength": str(min(Decimal("1.0"), v_buy / Decimal(str(strats_count[variant])))),
                                "strategy_name": "Consensus",
                                "timestamp_ms": current_time_ms,
                                "ab_variant": variant
                            }
                        elif v_sell >= MIN_CONSENSUS and v_sell > v_buy and suggested_prices_sell[variant]:
                            avg_price = sum(suggested_prices_sell[variant]) / Decimal(str(len(suggested_prices_sell[variant])))
                            final_signal = {
                                "type": "SELL",
                                "symbol": symbol,
                                "price": str(avg_price),
                                "strength": str(min(Decimal("1.0"), v_sell / Decimal(str(strats_count[variant])))),
                                "strategy_name": "Consensus",
                                "timestamp_ms": current_time_ms,
                                "ab_variant": variant
                            }

                        if final_signal:
                            self.last_signal_time[symbol] = current_time_ms
                            logger.info(f"[Variant {variant}] Signal: {final_signal['type']} {symbol}")
                            await self.redis_client.publish(f"signals:{symbol}", json.dumps(final_signal))
                            await self.redis_client.publish("signals", json.dumps(final_signal))

                except Exception as e:
                    logger.error(f"SignalEngine Loop Error: {e}")

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    engine = SignalEngine(redis_client)
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())

