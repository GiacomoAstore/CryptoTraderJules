import pytest
from unittest.mock import AsyncMock
from risk_manager.main import RiskManager

@pytest.fixture
def risk_manager():
    mock_redis = AsyncMock()
    rm = RiskManager(mock_redis)
    # Set initial state for testing
    rm.open_positions = 2
    rm.daily_pnl = 10.0
    rm.consecutive_losses = 1
    return rm

@pytest.mark.asyncio
async def test_process_trade_result_profitable(risk_manager):
    # Test a profitable trade
    trade_result = {"pnl_netto": 5.5}
    await risk_manager.process_trade_result(trade_result)

    assert risk_manager.open_positions == 1
    assert risk_manager.daily_pnl == 15.5
    assert risk_manager.consecutive_losses == 0

@pytest.mark.asyncio
async def test_process_trade_result_losing(risk_manager):
    # Test a losing trade
    trade_result = {"pnl_netto": -5.0}
    await risk_manager.process_trade_result(trade_result)

    assert risk_manager.open_positions == 1
    assert risk_manager.daily_pnl == 5.0
    assert risk_manager.consecutive_losses == 2

@pytest.mark.asyncio
async def test_process_trade_result_breakeven(risk_manager):
    # Test a breakeven trade
    trade_result = {"pnl_netto": 0.0}
    await risk_manager.process_trade_result(trade_result)

    assert risk_manager.open_positions == 1
    assert risk_manager.daily_pnl == 10.0
    assert risk_manager.consecutive_losses == 0

@pytest.mark.asyncio
async def test_process_trade_result_missing_pnl(risk_manager):
    # Test a trade result missing pnl_netto (defaults to 0)
    trade_result = {}
    await risk_manager.process_trade_result(trade_result)

    assert risk_manager.open_positions == 1
    assert risk_manager.daily_pnl == 10.0
    assert risk_manager.consecutive_losses == 0

@pytest.mark.asyncio
async def test_process_trade_result_open_positions_floor(risk_manager):
    # Test that open_positions doesn't go below 0
    risk_manager.open_positions = 0
    trade_result = {"pnl_netto": 5.0}
    await risk_manager.process_trade_result(trade_result)

    assert risk_manager.open_positions == 0
