import pytest
from unittest.mock import patch, MagicMock
import urllib.error
import json
from signal_engine.optimizer import fetch_historical_binance_data

def test_fetch_historical_binance_data_success():
    mock_data = [
        {"T": 1620000000000, "p": "50000.0", "q": "1.5"},
        {"T": 1620000001000, "p": "50001.0", "q": "0.5"}
    ]

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_data).encode('utf-8')
    mock_response.__enter__.return_value = mock_response

    with patch('urllib.request.urlopen', return_value=mock_response):
        result = fetch_historical_binance_data(symbol="BTCUSDT", limit=2)

    assert len(result) == 2
    assert result[0] == {
        "symbol": "BTCUSDT",
        "timestamp_ms": 1620000000000,
        "type": "trade",
        "price": 50000.0,
        "qty": 1.5
    }
    assert result[1] == {
        "symbol": "BTCUSDT",
        "timestamp_ms": 1620000001000,
        "type": "trade",
        "price": 50001.0,
        "qty": 0.5
    }

def test_fetch_historical_binance_data_error():
    with patch('urllib.request.urlopen', side_effect=urllib.error.URLError("Network error")):
        result = fetch_historical_binance_data(symbol="BTCUSDT", limit=1000)

    assert len(result) == 1000
    assert result[0] == {
        "symbol": "BTCUSDT",
        "timestamp_ms": 0,
        "type": "trade",
        "price": 60000
    }
    assert result[99] == {
        "symbol": "BTCUSDT",
        "timestamp_ms": 99,
        "type": "trade",
        "price": 60099
    }
