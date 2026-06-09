import pytest
from unittest.mock import AsyncMock, MagicMock
from api_gateway.repository import TimescaleTradeRepository

@pytest.mark.asyncio
async def test_insert_trade_no_pool():
    repo = TimescaleTradeRepository()
    repo.pool = None
    # Should return early and not raise any exceptions
    await repo.insert_trade({"order": {"type": "BUY"}})

@pytest.mark.asyncio
async def test_insert_trade_no_order():
    repo = TimescaleTradeRepository()
    repo.pool = MagicMock()
    # Should return early
    await repo.insert_trade({"not_order": 123})
    repo.pool.acquire.assert_not_called()

@pytest.mark.asyncio
async def test_insert_trade_empty_order():
    repo = TimescaleTradeRepository()
    repo.pool = MagicMock()
    # Should return early
    await repo.insert_trade({"order": {}})
    repo.pool.acquire.assert_not_called()

@pytest.mark.asyncio
async def test_insert_trade_success():
    repo = TimescaleTradeRepository()
    mock_pool = MagicMock()
    mock_conn = AsyncMock()

    # Mock context manager
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    repo.pool = mock_pool

    trade_data = {
        "order": {
            "type": "SELL",
            "symbol": "ethusdt",
            "price": 3000.5,
            "quantity": 2.5,
            "strategy": "MACD"
        },
        "pnl_netto": 15.0,
        "gross_pnl": 20.0,
        "commission_paid": 5.0,
        "close_reason": "TP"
    }

    await repo.insert_trade(trade_data)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]

    # Check query
    assert "INSERT INTO trades" in args[0]

    # Check arguments
    assert args[1] == "mock-3000.5" # trade_id
    assert args[2] == "ETHUSDT" # symbol
    assert args[3] == "SELL" # side
    assert args[4] == 3000.5 # price
    assert args[5] == 2.5 # quantity
    assert args[6] == "MACD" # strategy
    assert args[7] == 15.0 # pnl_netto
    assert args[8] == 20.0 # gross_pnl
    assert args[9] == 5.0 # commission_paid
    assert args[10] == "TP" # close_reason

@pytest.mark.asyncio
async def test_insert_trade_missing_fields():
    repo = TimescaleTradeRepository()
    mock_pool = MagicMock()
    mock_conn = AsyncMock()

    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    repo.pool = mock_pool

    trade_data = {
        "order": {
            "type": "BUY"
            # Missing other fields
        }
    }

    await repo.insert_trade(trade_data)

    mock_conn.execute.assert_called_once()
    args = mock_conn.execute.call_args[0]

    assert args[1] == "mock-None" # trade_id (price is None from get('price'))
    assert args[2] == "" # symbol
    assert args[3] == "BUY" # side
    assert args[4] == 0.0 # price
    assert args[5] == 0.01 # quantity
    assert args[6] == "Manual" # strategy
    assert args[7] is None # pnl_netto
    assert args[8] is None # gross_pnl
    assert args[9] is None # commission_paid
    assert args[10] is None # close_reason
