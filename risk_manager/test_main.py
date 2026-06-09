import pytest
from unittest.mock import AsyncMock
from risk_manager.main import RiskManager

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.get.return_value = None
    return mock

@pytest.fixture
def risk_manager(mock_redis):
    # Setting some environment variables implicitly by matching default class init behaviour,
    # or we could explicitly set them but the class has default values in __init__.
    # The default are MAX_OPEN_POSITIONS=3, MAX_CONSECUTIVE_LOSSES=5, MAX_DAILY_LOSS_USDT=50.0
    return RiskManager(mock_redis)

@pytest.mark.asyncio
async def test_evaluate_signal_global_circuit_breaker_open(risk_manager, mock_redis):
    mock_redis.get.return_value = "open"
    signal = {"symbol": "BTCUSDT"}

    result = await risk_manager.evaluate_signal(signal)

    assert result is False
    mock_redis.get.assert_called_once_with("risk:circuit_breaker")
    assert risk_manager.open_positions == 0 # unchanged

@pytest.mark.asyncio
async def test_evaluate_signal_max_open_positions_reached(risk_manager, mock_redis):
    mock_redis.get.return_value = None
    risk_manager.open_positions = risk_manager.MAX_OPEN_POSITIONS
    signal = {"symbol": "BTCUSDT"}

    result = await risk_manager.evaluate_signal(signal)

    assert result is False
    assert risk_manager.open_positions == risk_manager.MAX_OPEN_POSITIONS # unchanged

@pytest.mark.asyncio
async def test_evaluate_signal_max_consecutive_losses_reached(risk_manager, mock_redis):
    mock_redis.get.return_value = None
    risk_manager.consecutive_losses = risk_manager.MAX_CONSECUTIVE_LOSSES
    signal = {"symbol": "BTCUSDT"}

    result = await risk_manager.evaluate_signal(signal)

    assert result is False
    mock_redis.set.assert_called_once_with("risk:circuit_breaker", "open")
    assert risk_manager.open_positions == 0 # unchanged

@pytest.mark.asyncio
async def test_evaluate_signal_max_daily_loss_reached(risk_manager, mock_redis):
    mock_redis.get.return_value = None
    risk_manager.daily_pnl = -risk_manager.MAX_DAILY_LOSS_USDT
    signal = {"symbol": "BTCUSDT"}

    result = await risk_manager.evaluate_signal(signal)

    assert result is False
    mock_redis.set.assert_called_once_with("risk:circuit_breaker", "open")
    assert risk_manager.open_positions == 0 # unchanged

@pytest.mark.asyncio
async def test_evaluate_signal_approval(risk_manager, mock_redis):
    mock_redis.get.return_value = None
    signal = {"symbol": "BTCUSDT"}
    initial_open_positions = risk_manager.open_positions

    result = await risk_manager.evaluate_signal(signal)

    assert result is True
    assert risk_manager.open_positions == initial_open_positions + 1
