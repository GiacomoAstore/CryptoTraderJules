import asyncio
import json
import logging
import os
import sys
import time
import yaml
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, getcontext
import redis.asyncio as redis
import strategy
from market_filters import FilterParams, build_snapshot, passes_market_filters
from regime_filter import EmaTrendRegime

sys.path.insert(0, "/app/shared_config")
try:
    from validate_config import validate_config_dict
except ImportError:
    validate_config_dict = None

getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SignalEngine")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MIN_SIGNAL_INTERVAL_MS = int(os.getenv("MIN_SIGNAL_INTERVAL_MS", 500))


async def is_bot_running(redis_client) -> bool:
    status = await redis_client.get("bot:status")
    return status == "running"


def _hour_bucket(ts_ms: int) -> int:
    return ts_ms // 3_600_000


def _normalize_strategy_name(name: str) -> str:
    if name in ("EmaCrossoverStrategy", "EMAStrategy"):
        return "EMACrossoverStrategy"
    return name


@dataclass
class MomentumEmaLane:
    variant: str
    momentum: strategy.MomentumBurstStrategy
    fast_period: int
    slow_period: int
    ema_by_symbol: dict[str, EmaTrendRegime]

    def regime_for(self, symbol: str, min_sep_bps: Decimal) -> EmaTrendRegime:
        if symbol not in self.ema_by_symbol:
            self.ema_by_symbol[symbol] = EmaTrendRegime(
                self.fast_period, self.slow_period, min_sep_bps
            )
        return self.ema_by_symbol[symbol]


class SignalEngine:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.lanes: list[MomentumEmaLane] = []
        self.price_history = defaultdict(lambda: deque(maxlen=100))
        self.tick_history = defaultdict(lambda: deque(maxlen=100))
        self.last_signal_time = defaultdict(int)
        self.hour_signal_count: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.filter_params = FilterParams()
        self.ema_min_separation_bps = Decimal("3")
        self.max_signals_per_hour_per_symbol = 15
        self.setup_strategies()

    def setup_strategies(self):
        config_path = "/app/shared_config/config.yaml"
        self.lanes = []
        self.hour_signal_count = defaultdict(lambda: defaultdict(int))

        ema_variants: dict[str, dict] = {}
        momentum_entries: list[tuple[str, dict, Decimal]] = []

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    cfg = yaml.safe_load(f)

                if validate_config_dict and cfg:
                    validate_config_dict(cfg)

                if cfg and "signal_engine" in cfg:
                    se = cfg["signal_engine"] or {}
                    self.ema_min_separation_bps = Decimal(
                        str(se.get("ema_min_separation_bps", 3))
                    )
                    self.max_signals_per_hour_per_symbol = int(
                        se.get("max_signals_per_hour_per_symbol", 15)
                    )

                if cfg and "signal_filters" in cfg:
                    sf = cfg["signal_filters"]
                    self.filter_params = FilterParams(
                        max_spread_bps=Decimal(str(sf.get("max_spread_bps", 10))),
                        min_atr_pct=Decimal(str(sf.get("min_atr_pct", "0.0006"))),
                        max_atr_pct=Decimal(str(sf.get("max_atr_pct", "0.025"))),
                        min_volume_window=Decimal(str(sf.get("min_volume_window", 0))),
                        volume_window_ms=int(sf.get("volume_window_ms", 3000)),
                        max_chop_ratio=Decimal(str(sf.get("max_chop_ratio", 6))),
                        commission_rate=Decimal(str(sf.get("commission_rate", "0.001"))),
                        min_edge_vs_fees_mult=Decimal(
                            str(sf.get("min_edge_vs_fees_mult", "2.5"))
                        ),
                        min_expected_move_bps=Decimal(
                            str(sf.get("min_expected_move_bps", 12))
                        ),
                    )

                if cfg and "strategies" in cfg:
                    for s in cfg["strategies"]:
                        name = _normalize_strategy_name(s["name"])
                        if not s.get("enabled", True):
                            continue
                        weight = Decimal(str(s.get("weight", "1.0")))
                        if name == "EMACrossoverStrategy":
                            for variant_key, variant_name in [
                                ("params", "A"),
                                ("variant_a", "A"),
                                ("variant_b", "B"),
                            ]:
                                if variant_key in s:
                                    ema_variants[variant_name] = s[variant_key].copy()
                        elif name == "MomentumBurstStrategy":
                            for variant_key, variant_name in [
                                ("params", "A"),
                                ("variant_a", "A"),
                                ("variant_b", "B"),
                            ]:
                                if variant_key in s:
                                    params = s[variant_key].copy()
                                    params["weight"] = weight
                                    params["ab_variant"] = variant_name
                                    momentum_entries.append((variant_name, params, weight))

            except Exception as e:
                logger.error(f"Failed to load config.yaml: {e}")
                raise

        for variant_name, params, _weight in momentum_entries:
            ema_params = ema_variants.get(variant_name) or ema_variants.get("A")
            if not ema_params:
                logger.warning(
                    "No EMA params for variant %s — lane skipped", variant_name
                )
                continue
            fast = int(ema_params.get("fast_period", 8))
            slow = int(ema_params.get("slow_period", 21))
            mom = strategy.MomentumBurstStrategy(params)
            self.lanes.append(
                MomentumEmaLane(
                    variant=variant_name,
                    momentum=mom,
                    fast_period=fast,
                    slow_period=slow,
                    ema_by_symbol={},
                )
            )
            logger.info(
                "Loaded lane %s: MomentumBurst + EMA regime (fast=%s slow=%s sep>=%s bps)",
                variant_name,
                fast,
                slow,
                self.ema_min_separation_bps,
            )

        if not self.lanes:
            logger.warning("No momentum_ema lanes from config — using defaults")
            self.lanes.append(
                MomentumEmaLane(
                    variant="A",
                    momentum=strategy.MomentumBurstStrategy(
                        {
                            "weight": "0.9",
                            "ab_variant": "A",
                            "atr_mult": 1.25,
                            "window_ms": 3500,
                            "min_volume": 0.15,
                            "max_spread_bps": 10,
                        }
                    ),
                    fast_period=8,
                    slow_period=21,
                    ema_by_symbol={},
                )
            )

        logger.info(
            "Aggregation=momentum_ema_strict | lanes=%s | ema_sep>=%s bps | cap=%s/h/symbol",
            len(self.lanes),
            self.ema_min_separation_bps,
            self.max_signals_per_hour_per_symbol,
        )

    def _hourly_cap_ok(self, symbol: str, ts_ms: int) -> bool:
        cap = self.max_signals_per_hour_per_symbol
        if cap <= 0:
            return True
        bucket = _hour_bucket(ts_ms)
        return self.hour_signal_count[symbol][bucket] < cap

    def _record_hourly_signal(self, symbol: str, ts_ms: int) -> None:
        if self.max_signals_per_hour_per_symbol <= 0:
            return
        self.hour_signal_count[symbol][_hour_bucket(ts_ms)] += 1

    async def run(self):
        pubsub = self.redis_client.pubsub()
        await pubsub.psubscribe("ticks:*", "system:commands")
        logger.info("Signal Engine started. Waiting for ticks...")

        async for message in pubsub.listen():
            if message["type"] not in ("message", "pmessage"):
                continue

            channel = message.get("channel", "")

            if channel == "system:commands":
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                if data == "RELOAD_CONFIG":
                    logger.info("Received RELOAD_CONFIG command. Reloading...")
                    self.setup_strategies()
                continue

            if not channel.startswith("ticks:"):
                continue

            if not await is_bot_running(self.redis_client):
                continue

            try:
                tick_raw = json.loads(message["data"])
                symbol = tick_raw.get("symbol")
                if not symbol:
                    continue

                if float(tick_raw.get("price") or 0) <= 0:
                    continue

                tick: strategy.NormalizedTick = {
                    "symbol": symbol,
                    "price": Decimal(str(tick_raw["price"])),
                    "qty": Decimal(str(tick_raw["qty"])),
                    "side": tick_raw["side"],
                    "timestamp_ms": tick_raw["timestamp_ms"],
                    "bid_price": Decimal(str(tick_raw.get("bid_price", 0))),
                    "ask_price": Decimal(str(tick_raw.get("ask_price", 0))),
                    "bid_qty": Decimal(str(tick_raw.get("bid_qty", 0))),
                    "ask_qty": Decimal(str(tick_raw.get("ask_qty", 0))),
                }

                current_time_ms = int(time.time() * 1000)
                if current_time_ms - self.last_signal_time[symbol] < MIN_SIGNAL_INTERVAL_MS:
                    continue

                self.price_history[symbol].append(tick["price"])
                self.tick_history[symbol].append(tick)

                snapshot = build_snapshot(tick, self.tick_history[symbol])
                context = strategy.MarketContext(
                    price_history=self.price_history[symbol],
                    tick_history=self.tick_history[symbol],
                    current_position=None,
                    atr=snapshot.atr,
                    atr_pct=snapshot.atr_pct,
                    spread_bps=snapshot.spread_bps,
                )

                for lane in self.lanes:
                    disabled = await self.redis_client.get(
                        f"phase1:disable:{lane.momentum.name}"
                    )
                    if disabled:
                        continue

                    sig = lane.momentum.generate_signal(tick, context)
                    if not sig:
                        continue

                    ok, reason = passes_market_filters(
                        snapshot,
                        self.filter_params,
                        sig.expected_edge_bps,
                    )
                    if not ok:
                        logger.debug(
                            "Filter reject %s %s: %s",
                            lane.momentum.name,
                            symbol,
                            reason,
                        )
                        continue

                    ema_regime = lane.regime_for(symbol, self.ema_min_separation_bps)
                    aligned, sep_bps = ema_regime.aligned(tick["price"], sig.direction)
                    if not aligned:
                        logger.debug(
                            "EMA regime reject %s %s %s sep=%.2fbps",
                            symbol,
                            sig.direction,
                            lane.variant,
                            float(sep_bps),
                        )
                        continue

                    if not self._hourly_cap_ok(symbol, current_time_ms):
                        logger.debug(
                            "Hourly cap reject %s (max %s/h)",
                            symbol,
                            self.max_signals_per_hour_per_symbol,
                        )
                        continue

                    ok, _ = passes_market_filters(
                        snapshot, self.filter_params, sig.expected_edge_bps
                    )
                    if not ok:
                        continue

                    final_signal = {
                        "type": sig.direction,
                        "symbol": symbol,
                        "price": str(sig.suggested_price),
                        "strength": str(sig.strength),
                        "strategy_name": f"{lane.momentum.name}+EMARegime",
                        "voter_strategies": [lane.momentum.name, "EMARegime"],
                        "timestamp_ms": current_time_ms,
                        "ab_variant": lane.variant,
                        "expected_edge_bps": str(sig.expected_edge_bps),
                    }

                    self.last_signal_time[symbol] = current_time_ms
                    self._record_hourly_signal(symbol, current_time_ms)
                    logger.info(
                        "[Variant %s] Signal: %s %s (EMA sep %.2f bps)",
                        lane.variant,
                        final_signal["type"],
                        symbol,
                        float(sep_bps),
                    )
                    await self.redis_client.publish(
                        f"signals:{symbol}", json.dumps(final_signal)
                    )
                    await self.redis_client.publish("signals", json.dumps(final_signal))
                    break

            except Exception as e:
                logger.error(f"SignalEngine Loop Error: {e}")


async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    engine = SignalEngine(redis_client)
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
