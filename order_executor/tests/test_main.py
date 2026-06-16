import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import order_executor.main as main_module

@pytest.fixture(autouse=True)
def clear_state():
    main_module.open_positions.clear()
    main_module.shadow_open_positions.clear()
    yield
    main_module.open_positions.clear()
    main_module.shadow_open_positions.clear()

@pytest.mark.asyncio
async def test_paper_trade_command_execute():
    redis_client = AsyncMock()
    redis_client.get.return_value = None
    order_data = {"symbol": "BTCUSDT", "suggested_price": 50000, "suggested_qty": 1, "direction": "BUY"}

    cmd = main_module.PaperTradeCommand(order_data, redis_client)
    await cmd.execute()

    assert "BTCUSDT" in main_module.open_positions
    assert len(main_module.open_positions["BTCUSDT"]) == 1
    assert main_module.open_positions["BTCUSDT"][0]["qty"] == 1
    assert main_module.open_positions["BTCUSDT"][0]["entry_price"] == 50000
    redis_client.set.assert_called_once()

@pytest.mark.asyncio
async def test_paper_trade_command_undo():
    redis_client = AsyncMock()
    order_data = {"symbol": "BTCUSDT"}
    cmd = main_module.PaperTradeCommand(order_data, redis_client)

    cmd.position = {"id": "123", "symbol": "BTCUSDT"}
    main_module.open_positions["BTCUSDT"] = [cmd.position]

    await cmd.undo()
    assert len(main_module.open_positions["BTCUSDT"]) == 0

@pytest.mark.asyncio
async def test_evaluate_open_positions_sl_hit():
    redis_client = AsyncMock()
    redis_client.get.return_value = "10000.0"

    import time
    pos = {
        "id": "123", "symbol": "BTCUSDT", "side": "BUY", "qty": 1,
        "entry_price": 50000, "entry_time": int(time.time() * 1000),
        "stop_loss": 49000, "take_profit": 51000, "strategy": "test"
    }
    main_module.open_positions["BTCUSDT"] = [pos]

    tick = {"symbol": "BTCUSDT", "price": 48000}
    await main_module.evaluate_open_positions(tick, redis_client)

    assert len(main_module.open_positions["BTCUSDT"]) == 0
    assert redis_client.publish.call_count == 2

@pytest.mark.asyncio
async def test_validate_historical_performance_pass():
    redis_client = AsyncMock()

    class MockResponse:
        def __init__(self, status, json_data):
            self.status = status
            self._json_data = json_data
        async def json(self):
            return self._json_data
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass

    class MockSession:
        def get(self, url):
            return MockResponse(200, {"metrics": {"win_rate": 45.0, "max_drawdown": -100.0}})
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass

    with patch("aiohttp.ClientSession", return_value=MockSession()):
        result = await main_module.validate_historical_performance(redis_client)
        assert result is True

@pytest.mark.asyncio
async def test_main_kill_switch():
    redis_client = MagicMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.set = AsyncMock()

    mock_pubsub = MagicMock()
    mock_pubsub.psubscribe = AsyncMock()

    async def mock_listen():
        yield {"type": "message", "channel": "system_commands", "data": '{"action": "KILL_SWITCH"}'}
        raise asyncio.CancelledError()

    mock_pubsub.listen = mock_listen
    redis_client.pubsub.return_value = mock_pubsub

    with patch("order_executor.main.redis.Redis", return_value=redis_client):
        try:
            await main_module.main()
        except asyncio.CancelledError:
            pass

    assert len(main_module.open_positions) == 0
    redis_client.set.assert_called_with("state:open_positions", "{}")
