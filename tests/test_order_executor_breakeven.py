import asyncio
from decimal import Decimal
import pytest

from order_executor.main import PaperEngine, OrderCommand


class DummyRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)

    async def expire(self, key, seconds):
        pass

    async def publish(self, channel, payload):
        pass


class DummyDBConn:
    def __init__(self, recorder):
        self.recorder = recorder

    async def execute(self, query, *args):
        # record executed updates for assertions
        self.recorder.append((query, args))
    
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyDBPool:
    def __init__(self, recorder):
        self.recorder = recorder

    class _Acquirer:
        def __init__(self, recorder):
            self.recorder = recorder

        async def __aenter__(self):
            return DummyDBConn(self.recorder)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def acquire(self):
        return DummyDBPool._Acquirer(self.recorder)

    # context manager support
    def __await__(self):
        yield


@pytest.mark.asyncio
async def test_breakeven_sets_stop_once():
    redis = DummyRedis()
    db_calls = []
    db_pool = DummyDBPool(db_calls)

    engine = PaperEngine(redis, db_pool)

    # create a filled position
    data = {
        "symbol": "BTCUSDT",
        "type": "BUY",
        "price": "100",
        "quantity": "1",
        "ab_variant": "A",
        "atr_bps": 1.0,
    }
    cmd = OrderCommand(data)
    cmd.status = "FILLED"
    cmd.executed_price = Decimal("100")
    cmd.stop_loss = None
    cmd.ab_variant = "A"

    pos_key = f"{cmd.symbol}_{cmd.ab_variant}"
    engine.open_positions[pos_key] = cmd

    # set internal ATR to a large value so it would NOT trigger at 101
    engine.atr_by_symbol["BTCUSDT"] = Decimal("5")

    # tick that should trigger breakeven
    await engine.monitor_ticks({"symbol": "BTCUSDT", "price": 101})

    assert cmd.breakeven_set is True
    assert cmd.stop_loss is not None

    # Check Redis persisted flags
    assert redis.store.get(f"position:breakeven:{pos_key}") == "1"
    assert redis.store.get(f"position:stop_loss:{pos_key}") is not None

    # DB should have been updated once
    assert len(db_calls) == 1

    # second tick should not cause another DB update
    await engine.monitor_ticks({"symbol": "BTCUSDT", "price": 102})
    assert len(db_calls) == 1


@pytest.mark.asyncio
async def test_breakeven_for_sell():
    redis = DummyRedis()
    db_calls = []
    db_pool = DummyDBPool(db_calls)

    engine = PaperEngine(redis, db_pool)

    data = {
        "symbol": "BTCUSDT",
        "type": "SELL",
        "price": "200",
        "quantity": "1",
        "ab_variant": "A",
        "atr_bps": 1.0,
    }
    cmd = OrderCommand(data)
    cmd.status = "FILLED"
    cmd.executed_price = Decimal("200")
    cmd.stop_loss = None
    cmd.ab_variant = "A"

    pos_key = f"{cmd.symbol}_{cmd.ab_variant}"
    engine.open_positions[pos_key] = cmd

    # set internal ATR large; payload ATR small
    engine.atr_by_symbol["BTCUSDT"] = Decimal("10")

    # tick that should trigger breakeven for SELL: price <= 198
    await engine.monitor_ticks({"symbol": "BTCUSDT", "price": 198})

    assert cmd.breakeven_set is True
    assert cmd.stop_loss is not None
    assert redis.store.get(f"position:breakeven:{pos_key}") == "1"
    assert len(db_calls) == 1
