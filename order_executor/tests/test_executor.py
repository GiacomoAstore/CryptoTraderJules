import pytest
import pytest_asyncio
import asyncio
import time
import json
from unittest.mock import AsyncMock, patch

from order_executor.main import evaluate_open_positions, _evaluate_pool, open_positions, shadow_open_positions

@pytest.fixture
def redis_client():
    client = AsyncMock()
    return client

@pytest.fixture(autouse=True)
def clear_positions():
    open_positions.clear()
    shadow_open_positions.clear()
    yield
    open_positions.clear()
    shadow_open_positions.clear()

@pytest.mark.asyncio
async def test_evaluate_open_positions_invalid_price(redis_client):
    tick = {"symbol": "BTCUSDT", "price": -100.0}
    await evaluate_open_positions(tick, redis_client)
    redis_client.set.assert_not_called()

@pytest.mark.asyncio
async def test_evaluate_open_positions_no_positions(redis_client):
    tick = {"symbol": "BTCUSDT", "price": 60000.0}
    await evaluate_open_positions(tick, redis_client)
    redis_client.set.assert_not_called()

def create_mock_position(pos_id="1", symbol="BTCUSDT", side="BUY", qty=1.0, entry_price=60000.0, age_ms=0):
    sl_mult = 0.995 if side == "BUY" else 1.005
    tp_mult = 1.005 if side == "BUY" else 0.995
    return {
        "id": pos_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": entry_price,
        "entry_time": int(time.time() * 1000) - age_ms,
        "stop_loss": entry_price * sl_mult,
        "take_profit": entry_price * tp_mult,
        "strategy": "TestStrategy"
    }

@pytest.mark.asyncio
async def test_evaluate_buy_position_tp_hit(redis_client):
    pos = create_mock_position(side="BUY", entry_price=60000.0)
    open_positions["BTCUSDT"] = [pos]
    redis_client.get.return_value = "10000.0"

    tick = {"symbol": "BTCUSDT", "price": 60500.0} # TP is 60300
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 0
    redis_client.get.assert_called_with("paper:balance")
    redis_client.set.assert_any_call("paper:balance", 10000.0 + (500.0 - (60000.0*0.001 + 60500.0*0.001))) # Net PNL
    redis_client.publish.assert_any_call("executed_trades", json.dumps({
        "status": "FILLED",
        "order": {
            "symbol": "BTCUSDT",
            "type": "SELL",
            "price": 60500.0,
            "quantity": 1.0,
            "strategy": "TestStrategy"
        },
        "close_reason": "TP_HIT",
        "gross_pnl": 500.0,
        "commission_paid": 120.5,
        "pnl_netto": 500.0 - 120.5
    }))

@pytest.mark.asyncio
async def test_evaluate_buy_position_sl_hit(redis_client):
    pos = create_mock_position(side="BUY", entry_price=60000.0)
    open_positions["BTCUSDT"] = [pos]
    redis_client.get.return_value = "10000.0"

    tick = {"symbol": "BTCUSDT", "price": 59000.0} # SL is 59700
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 0

@pytest.mark.asyncio
async def test_evaluate_sell_position_tp_hit(redis_client):
    pos = create_mock_position(side="SELL", entry_price=60000.0)
    open_positions["BTCUSDT"] = [pos]
    redis_client.get.return_value = "10000.0"

    tick = {"symbol": "BTCUSDT", "price": 59000.0} # TP is 59700
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 0

@pytest.mark.asyncio
async def test_evaluate_sell_position_sl_hit(redis_client):
    pos = create_mock_position(side="SELL", entry_price=60000.0)
    open_positions["BTCUSDT"] = [pos]
    redis_client.get.return_value = "10000.0"

    tick = {"symbol": "BTCUSDT", "price": 61000.0} # SL is 60300
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 0

@pytest.mark.asyncio
async def test_evaluate_position_timeout(redis_client):
    pos = create_mock_position(side="BUY", entry_price=60000.0, age_ms=300001) # > 5 mins
    open_positions["BTCUSDT"] = [pos]
    redis_client.get.return_value = "10000.0"

    tick = {"symbol": "BTCUSDT", "price": 60000.0}
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 0

@pytest.mark.asyncio
async def test_evaluate_position_no_hit(redis_client):
    pos = create_mock_position(side="BUY", entry_price=60000.0)
    open_positions["BTCUSDT"] = [pos]

    tick = {"symbol": "BTCUSDT", "price": 60000.0}
    await evaluate_open_positions(tick, redis_client)

    assert len(open_positions["BTCUSDT"]) == 1

@pytest.mark.asyncio
async def test_evaluate_shadow_position(redis_client):
    pos = create_mock_position(side="BUY", entry_price=60000.0)
    shadow_open_positions["BTCUSDT"] = [pos]

    tick = {"symbol": "BTCUSDT", "price": 60500.0} # TP is 60300
    await evaluate_open_positions(tick, redis_client)

    assert len(shadow_open_positions["BTCUSDT"]) == 0
    # Shadow positions do not update balance
    redis_client.get.assert_not_called()
