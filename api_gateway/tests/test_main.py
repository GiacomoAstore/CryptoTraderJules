import pytest
from fastapi.testclient import TestClient
import os

import sys
import os

# Add api_gateway to path so we can import main
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

# Set up test client
client = TestClient(app)

# Default test API key, this is set in main.py if the environment variable is not defined
TEST_API_KEY = "default_api_key"

def test_read_root():
    # Root endpoint should not require authentication
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_get_trades_no_api_key():
    response = client.get("/api/trades")
    assert response.status_code == 403
    assert response.json()["detail"] == "Could not validate credentials"

def test_get_trades_invalid_api_key():
    response = client.get("/api/trades", headers={"X-API-Key": "invalid_key"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Could not validate credentials"

# Assuming mocked trade_repo for full 200 checks, but we only need to test the dependency logic
@pytest.mark.asyncio
async def test_get_trades_valid_api_key(mocker):
    # Mock trade_repo.get_recent_trades to avoid database dependency in tests
    from main import trade_repo
    mocker.patch.object(trade_repo, "get_recent_trades", return_value=[])

    response = client.get("/api/trades", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_get_metrics_valid_api_key(mocker):
    # Metrics relies on Redis and DB
    import redis.asyncio as redis
    from main import trade_repo

    mocker.patch("redis.asyncio.Redis", autospec=True)
    mocker.patch.object(trade_repo, "pool", new=None)

    response = client.get("/api/metrics", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200

def test_kill_switch_no_api_key():
    response = client.post("/api/kill-switch")
    assert response.status_code == 403
    assert response.json()["detail"] == "Could not validate credentials"

def test_kill_switch_invalid_api_key():
    response = client.post("/api/kill-switch", headers={"X-API-Key": "invalid_key"})
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_kill_switch_valid_api_key(mocker):
    # Mock redis to prevent actual publishing
    import redis.asyncio as redis
    mock_redis = mocker.AsyncMock()
    mocker.patch("redis.asyncio.Redis", return_value=mock_redis)

    response = client.post("/api/kill-switch", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
