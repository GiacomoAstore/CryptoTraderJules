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


@pytest.mark.asyncio
async def test_resume_candle_state_after_restart():
    redis_client = DummyRedis()
    active_key = "signal_engine:1m_candle:BTCUSDT"
    history_key = "signal_engine:1m_candle_history:BTCUSDT"

    active_candle = CandleState(
        symbol="BTCUSDT",
        start_ts_ms=1_700_000_000_000,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("0.7"),
        last_tick_ts_ms=1_700_000_000_900,
    )
    redis_client.data[active_key] = active_candle.to_json()

    previous = CandleState(
        symbol="BTCUSDT",
        start_ts_ms=1_699_999_940_000,
        open=Decimal("98"),
        high=Decimal("100"),
        low=Decimal("97"),
        close=Decimal("99"),
        volume=Decimal("1.2"),
        last_tick_ts_ms=1_699_999_999_900,
    )
    redis_client.lists[history_key] = [previous.to_json()]

    engine = Breakout1mEngine(redis_client, {"breakout_lookback": 20, "volume_ma_multiplier": 1.0})
    await engine.initialize()

    assert "BTCUSDT" in engine.candle_state
    assert engine.candle_state["BTCUSDT"].open == Decimal("100")
    assert "BTCUSDT" in engine.candle_history
    assert len(engine.candle_history["BTCUSDT"]) == 1
    assert engine.candle_history["BTCUSDT"][0].high == Decimal("100")
