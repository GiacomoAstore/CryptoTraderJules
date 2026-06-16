from fastapi.testclient import TestClient
from api_gateway.main import app

client = TestClient(app)

def test_get_trades_limit_validation():
    # Valid limit should proceed (might fail further down if db isn't up, but validation should pass)
    # So we expect something other than 422 if limit is valid, but let's test invalid first
    response = client.get("/api/trades?limit=101")
    assert response.status_code == 422, "Expected 422 Unprocessable Entity for limit > 100"

    response = client.get("/api/trades?limit=0")
    assert response.status_code == 422, "Expected 422 Unprocessable Entity for limit < 1"

def test_get_trades_by_symbol_limit_validation():
    response = client.get("/api/trades/BTCUSDT?limit=101")
    assert response.status_code == 422, "Expected 422 Unprocessable Entity for limit > 100"

    response = client.get("/api/trades/BTCUSDT?limit=0")
    assert response.status_code == 422, "Expected 422 Unprocessable Entity for limit < 1"
