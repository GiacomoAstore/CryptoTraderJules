import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from api_gateway.main import compute_daily_performance, trade_repo

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    # pool.acquire returns an async context manager
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn

@pytest.mark.asyncio
async def test_compute_daily_performance_no_pool():
    """Test that it handles a missing DB pool gracefully."""
    with patch("api_gateway.main.trade_repo.pool", None):
        # Should return early without throwing an error
        await compute_daily_performance()

@pytest.mark.asyncio
async def test_compute_daily_performance_no_trades(mock_pool):
    """Test when there are no trades for the day."""
    pool, conn = mock_pool
    # Mocking first fetch to return 0 trades
    conn.fetch.return_value = [{"total_trades": 0}]

    with patch("api_gateway.main.trade_repo.pool", pool):
        await compute_daily_performance()

    conn.fetch.assert_called_once()
    conn.execute.assert_not_called()

@pytest.mark.asyncio
async def test_compute_daily_performance_with_trades(mock_pool):
    """Test computation and redis publishing with valid trades."""
    pool, conn = mock_pool

    # We need fetch to return different things on successive calls
    conn.fetch.side_effect = [
        [{"total_trades": 10, "total_pnl": 50.5, "winning_trades": 6}], # stats
        [{"max_loss": -15.0}], # drawdown
        [{"mean_pnl": 5.0, "std_pnl": 2.0}] # sharpe
    ]

    mock_redis = AsyncMock()
    mock_redis_class = MagicMock(return_value=mock_redis)

    with patch("api_gateway.main.trade_repo.pool", pool), \
         patch("api_gateway.main.redis.Redis", mock_redis_class):
        await compute_daily_performance()

    assert conn.fetch.call_count == 3
    conn.execute.assert_called_once()

    # Verify redis calls
    mock_redis.publish.assert_called_once()
    mock_redis.aclose.assert_called_once()

    args, _ = mock_redis.publish.call_args
    assert args[0] == "alerts"
    msg_data = json.loads(args[1])
    assert "Daily Report" in msg_data["message"]
    assert "PnL: 50.50" in msg_data["message"]
    assert "Win Rate: 60.0%" in msg_data["message"]
    assert "Max Drawdown: -15.00" in msg_data["message"]
    assert "Sharpe: 2.50" in msg_data["message"]

@pytest.mark.asyncio
async def test_compute_daily_performance_exception(mock_pool):
    """Test that exceptions during computation are handled gracefully."""
    pool, conn = mock_pool
    # Simulate DB error
    conn.fetch.side_effect = Exception("DB Connection Lost")

    with patch("api_gateway.main.trade_repo.pool", pool):
        await compute_daily_performance()
        # Should not crash, just print error
