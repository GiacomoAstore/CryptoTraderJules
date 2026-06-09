import pytest
from unittest.mock import AsyncMock, MagicMock
from api_gateway.repository import TimescaleTradeRepository

@pytest.fixture
def repository():
    return TimescaleTradeRepository()

@pytest.mark.asyncio
async def test_get_recent_trades_without_pool(repository):
    repository.pool = None
    trades = await repository.get_recent_trades(limit=5)

    assert len(trades) == 1
    assert trades[0]["id"] == "mock-1"
    assert trades[0]["symbol"] == "BTCUSDT"

@pytest.mark.asyncio
async def test_get_recent_trades_with_pool(repository):
    # Mock pool and connection
    mock_pool = MagicMock()
    mock_conn = AsyncMock()

    # Mock row objects returned by fetch
    mock_row1 = {"id": 1, "symbol": "BTCUSDT", "price": 65000.0}
    mock_row2 = {"id": 2, "symbol": "ETHUSDT", "price": 3500.0}

    # Setup mock_conn.fetch to return the mock rows
    mock_conn.fetch.return_value = [mock_row1, mock_row2]

    # Context manager setup for acquire()
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value = mock_acquire

    repository.pool = mock_pool

    trades = await repository.get_recent_trades(limit=10)

    assert len(trades) == 2
    assert trades[0]["id"] == 1
    assert trades[1]["symbol"] == "ETHUSDT"

    # Verify fetch was called correctly
    mock_conn.fetch.assert_called_once_with("SELECT * FROM trades ORDER BY time DESC LIMIT $1", 10)
