import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "risk_manager"))

from risk_manager.main import RiskManager


class DummyRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.mark.asyncio
async def test_restore_state_from_redis():
    redis_client = DummyRedis()
    redis_client.data["risk:open_positions"] = json.dumps({
        "BTCUSDT_A": {"symbol": "BTCUSDT", "status": "OPEN"}
    })
    redis_client.data["risk:pending_orders"] = json.dumps({
        "ETHUSDT_A": {"symbol": "ETHUSDT", "status": "PENDING"}
    })

    rm = RiskManager(redis_client)
    await rm.restore_state()

    assert rm.open_positions["BTCUSDT_A"]["symbol"] == "BTCUSDT"
    assert rm.pending_orders["ETHUSDT_A"]["symbol"] == "ETHUSDT"
