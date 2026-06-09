import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from api_gateway.main import app

client = TestClient(app)

@patch("api_gateway.main.trade_repo.get_recent_trades", new_callable=AsyncMock)
def test_get_trades_success(mock_get_recent_trades):
    # Setup mock return value
    mock_trades = [
        {"id": "trade1", "symbol": "BTCUSDT", "price": 50000.0},
        {"id": "trade2", "symbol": "ETHUSDT", "price": 3000.0}
    ]
    mock_get_recent_trades.return_value = mock_trades

    # Call endpoint
    response = client.get("/api/trades?limit=10")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["trades"] == mock_trades
    mock_get_recent_trades.assert_called_once_with(limit=10)

@patch("api_gateway.main.trade_repo.get_recent_trades", new_callable=AsyncMock)
def test_get_trades_default_limit(mock_get_recent_trades):
    mock_get_recent_trades.return_value = []

    response = client.get("/api/trades")

    assert response.status_code == 200
    mock_get_recent_trades.assert_called_once_with(limit=50)

@patch("api_gateway.main.trade_repo.get_recent_trades", new_callable=AsyncMock)
def test_get_trades_invalid_limit(mock_get_recent_trades):
    response = client.get("/api/trades?limit=invalid")

    # FastAPI validation should return 422 Unprocessable Entity
    assert response.status_code == 422
