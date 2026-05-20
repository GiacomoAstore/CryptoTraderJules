import pytest
from decimal import Decimal
from unittest.mock import AsyncMock

from risk_manager.risk_config import _apply_yaml_risk, _from_env


def test_yaml_risk_overrides_env_defaults():
    params = _from_env()
    params = _apply_yaml_risk(params, {
        "max_open_positions": 5,
        "max_daily_loss_usdt": 99.0,
        "stop_loss_atr_multiplier": 2.5,
    })
    assert params.max_open_positions == 5
    assert params.max_daily_loss_usdt == Decimal("99.0")
    assert params.stop_loss_atr_multiplier == Decimal("2.5")


@pytest.mark.asyncio
async def test_redis_config_overrides_yaml():
    from risk_manager.risk_config import load_risk_params

    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value='{"MAX_DAILY_LOSS_USDT": 42}')

    params = await load_risk_params(redis_client)
    assert params.max_daily_loss_usdt == Decimal("42")
