import asyncio
from collections import deque
from decimal import Decimal

from signal_engine.breakout_1m import Breakout1mEngine
from signal_engine.candles import CandleState


class DummyRedis:
    def __init__(self):
        self.data = {}
        self.lists = {}
        self.published = []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def keys(self, pattern):
        if pattern == "signal_engine:1m_candle:*":
            return [k for k in self.data if k.startswith("signal_engine:1m_candle:")]
        if pattern == "signal_engine:1m_candle_history:*":
            return [k for k in self.lists if k.startswith("signal_engine:1m_candle_history:")]
        return []

    async def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start:stop + 1]

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        self.lists[key] = self.lists.get(key, [])[start:stop + 1]

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


async def main():
    redis = DummyRedis()
    engine = Breakout1mEngine(
        redis,
        {
            "breakout_lookback": 3,
            "volume_ma_multiplier": 1.0,
            "breakout_buffer_bps": 0,
            "breakout_enabled": True,
        },
    )

    historical = [
        CandleState(
            symbol="BTCUSDT",
            start_ts_ms=1700000000000 - 120000,
            open=Decimal("99"),
            high=Decimal("101"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("1.0"),
            last_tick_ts_ms=1700000000000 - 60000,
        ),
        CandleState(
            symbol="BTCUSDT",
            start_ts_ms=1700000000000 - 60000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1.2"),
            last_tick_ts_ms=1700000000000 - 1,
        ),
    ]
    engine.candle_history["BTCUSDT"] = deque(historical, maxlen=3)

    ticks = [
        {"symbol": "BTCUSDT", "price": 101.0, "qty": 0.3, "timestamp_ms": 1700000000000},
        {"symbol": "BTCUSDT", "price": 103.0, "qty": 0.8, "timestamp_ms": 1700000000000 + 30000},
        {"symbol": "BTCUSDT", "price": 103.0, "qty": 0.1, "timestamp_ms": 1700000000000 + 60000},
    ]

    for t in ticks:
        await engine.process_tick(t)

    print("published", redis.published)
    print("active", engine.candle_state.get("BTCUSDT"))
    print("history len", len(engine.candle_history["BTCUSDT"]))


asyncio.run(main())
