import json
from collections import deque
from decimal import Decimal

import pytest

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
        values = self.lists.get(key, [])
        return values[start : stop + 1]

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        values = self.lists.get(key, [])
        self.lists[key] = values[start : stop + 1]

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_aggregate_1m_candle_open_high_low_close_volume():
    tick1 = {
        "symbol": "BTCUSDT",
        "price": 100.0,
        "qty": 0.5,
        "timestamp_ms": 1_700_000_000_000,
    }
    tick2 = {
        "symbol": "BTCUSDT",
        "price": 102.0,
        "qty": 0.25,
        "timestamp_ms": 1_700_000_000_500,
    }
    tick3 = {
        "symbol": "BTCUSDT",
        "price": 99.0,
        "qty": 0.75,
        "timestamp_ms": 1_700_000_000_900,
    }

    candle = CandleState.from_tick(tick1)
    candle.update(tick2)
    candle.update(tick3)

    assert candle.open == Decimal("100.0")
    assert candle.high == Decimal("102.0")
    assert candle.low == Decimal("99.0")
    assert candle.close == Decimal("99.0")
    assert candle.volume == Decimal("1.5")


@pytest.mark.asyncio
async def test_generate_breakout_signal_on_high_break():
    redis_client = DummyRedis()
    engine = Breakout1mEngine(
        redis_client,
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
            start_ts_ms=1_700_000_000_000 - 120_000,
            open=Decimal("99"),
            high=Decimal("101"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("1.0"),
            last_tick_ts_ms=1_700_000_000_000 - 60_000,
        ),
        CandleState(
            symbol="BTCUSDT",
            start_ts_ms=1_700_000_000_000 - 60_000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1.2"),
            last_tick_ts_ms=1_700_000_000_000 - 1,
        ),
    ]
    engine.candle_history["BTCUSDT"] = deque(historical, maxlen=3)

    first_tick = {
        "symbol": "BTCUSDT",
        "price": 101.0,
        "qty": 0.3,
        "timestamp_ms": 1_700_000_000_000,
    }
    second_tick = {
        "symbol": "BTCUSDT",
        "price": 103.0,
        "qty": 1.0,
        "timestamp_ms": 1_700_000_000_000 + 30_000,
    }
    third_tick = {
        "symbol": "BTCUSDT",
        "price": 103.0,
        "qty": 0.1,
        "timestamp_ms": 1_700_000_000_000 + 60_000,
    }

    await engine.process_tick(first_tick)
    await engine.process_tick(second_tick)
    await engine.process_tick(third_tick)

    assert any(ch == "signals:BTCUSDT" for ch, _ in redis_client.published)
    assert any(ch == "signals" for ch, _ in redis_client.published)
    payload = json.loads(redis_client.published[0][1])
    assert payload["type"] == "BUY"
    assert payload["strategy_name"] == "Breakout1m"
    assert payload["signal_source"] == "breakout_1m"


@pytest.mark.asyncio
async def test_no_signal_below_volume_ma_multiplier():
    redis_client = DummyRedis()
    engine = Breakout1mEngine(
        redis_client,
        {
            "breakout_lookback": 3,
            "volume_ma_multiplier": 2.0,
            "breakout_buffer_bps": 0,
            "breakout_enabled": True,
        },
    )

    historical = [
        CandleState(
            symbol="BTCUSDT",
            start_ts_ms=1_700_000_000_000 - 120_000,
            open=Decimal("99"),
            high=Decimal("100"),
            low=Decimal("98"),
            close=Decimal("99.5"),
            volume=Decimal("1.0"),
            last_tick_ts_ms=1_700_000_000_000 - 60_000,
        ),
        CandleState(
            symbol="BTCUSDT",
            start_ts_ms=1_700_000_000_000 - 60_000,
            open=Decimal("99.5"),
            high=Decimal("100.5"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1.0"),
            last_tick_ts_ms=1_700_000_000_000 - 1,
        ),
    ]
    engine.candle_history["BTCUSDT"] = deque(historical, maxlen=3)

    await engine.process_tick(
        {
            "symbol": "BTCUSDT",
            "price": 101.0,
            "qty": 1.0,
            "timestamp_ms": 1_700_000_000_000,
        }
    )
    await engine.process_tick(
        {
            "symbol": "BTCUSDT",
            "price": 102.0,
            "qty": 0.5,
            "timestamp_ms": 1_700_000_000_000 + 60_000,
        }
    )

    assert len(redis_client.published) == 0
