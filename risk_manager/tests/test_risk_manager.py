import pytest
from unittest.mock import AsyncMock, patch
from risk_manager.main import RiskManager

@pytest.fixture
def mock_redis():
    return AsyncMock()

@pytest.fixture
def risk_manager(mock_redis):
    rm = RiskManager(mock_redis)
    # Reset default values to ensure predictable test environment
    rm.MAX_OPEN_POSITIONS = 3
    rm.MAX_CONSECUTIVE_LOSSES = 5
    rm.MAX_DAILY_LOSS_USDT = 50.0
    return rm

@pytest.mark.asyncio
async def test_evaluate_signal_global_circuit_breaker_open(risk_manager, mock_redis):
    # Setup
    mock_redis.get.return_value = "open"
    signal = {"symbol": "BTCUSDT"}

    # Execute
    result = await risk_manager.evaluate_signal(signal)

    # Assert
    assert result is False
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")

@pytest.mark.asyncio
async def test_evaluate_signal_max_open_positions_reached(risk_manager, mock_redis):
    # Setup
    mock_redis.get.return_value = None
    risk_manager.open_positions = risk_manager.MAX_OPEN_POSITIONS
    signal = {"symbol": "BTCUSDT"}

    # Execute
    result = await risk_manager.evaluate_signal(signal)

    # Assert
    assert result is False
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")

@pytest.mark.asyncio
async def test_evaluate_signal_max_consecutive_losses_reached(risk_manager, mock_redis):
    # Setup
    mock_redis.get.return_value = None
    risk_manager.open_positions = 0
    risk_manager.consecutive_losses = risk_manager.MAX_CONSECUTIVE_LOSSES
    signal = {"symbol": "BTCUSDT"}

    # Execute
    result = await risk_manager.evaluate_signal(signal)

    # Assert
    assert result is False
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")
    mock_redis.set.assert_called_once_with("risk:circuit_breaker", "open")

@pytest.mark.asyncio
async def test_evaluate_signal_max_daily_loss_reached(risk_manager, mock_redis):
    # Setup
    mock_redis.get.return_value = None
    risk_manager.open_positions = 0
    risk_manager.consecutive_losses = 0
    risk_manager.daily_pnl = -risk_manager.MAX_DAILY_LOSS_USDT - 10.0 # Exceeded limit
    signal = {"symbol": "BTCUSDT"}

    # Execute
    result = await risk_manager.evaluate_signal(signal)

    # Assert
    assert result is False
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")
    mock_redis.set.assert_called_once_with("risk:circuit_breaker", "open")

@pytest.mark.asyncio
async def test_evaluate_signal_normal_case_approved(risk_manager, mock_redis):
    # Setup
    mock_redis.get.return_value = None
    risk_manager.open_positions = 0
    risk_manager.consecutive_losses = 0
    risk_manager.daily_pnl = 10.0 # Profitable day
    signal = {"symbol": "BTCUSDT"}

    # Execute
    result = await risk_manager.evaluate_signal(signal)

    # Assert
    assert result is True
    assert risk_manager.open_positions == 1
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")
    mock_redis.set.assert_not_called()
