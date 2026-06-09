import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from api_gateway.main import app

client = TestClient(app)

@pytest.fixture
def mock_redis():
    with patch("api_gateway.main.redis.Redis") as mock:
        mock_instance = AsyncMock()
        mock.return_value = mock_instance
        yield mock_instance

def test_trigger_kill_switch_success(mock_redis):
    response = client.post("/api/kill-switch")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Kill switch activated"}

    assert mock_redis.publish.call_count == 2

    # Check first call (system_commands)
    args, kwargs = mock_redis.publish.call_args_list[0]
    assert args[0] == "system_commands"
    assert '"action": "KILL_SWITCH"' in args[1]

    # Check second call (alerts)
    args, kwargs = mock_redis.publish.call_args_list[1]
    assert args[0] == "alerts"
    assert "KILL SWITCH ACTIVATED" in args[1]

    mock_redis.aclose.assert_called_once()

def test_trigger_kill_switch_error(mock_redis):
    mock_redis.publish.side_effect = Exception("Redis connection error")

    response = client.post("/api/kill-switch")

    assert response.status_code == 200
    assert response.json() == {"status": "error", "message": "Redis connection error"}
