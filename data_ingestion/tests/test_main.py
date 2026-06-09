import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch
from data_ingestion.main import binance_websocket_consumer

class MockWebSocket:
    def __init__(self, messages, error_to_raise=None):
        self.messages = messages
        self.message_index = 0
        self.error_to_raise = error_to_raise

    async def recv(self):
        if self.error_to_raise is not None:
            err = self.error_to_raise
            self.error_to_raise = None
            raise err

        if self.message_index < len(self.messages):
            msg = self.messages[self.message_index]
            self.message_index += 1
            return json.dumps(msg)
        else:
            # Raise CancelledError to gracefully exit the infinite loop during testing
            raise asyncio.CancelledError()

class MockConnect:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_binance_websocket_consumer_trade():
    mock_redis = AsyncMock()

    # Simulate a trade message
    trade_message = {
        "stream": "btcusdt@trade",
        "data": {
            "s": "BTCUSDT",
            "E": 1623456789000,
            "p": "50000.00",
            "q": "0.1",
            "m": True # m=True means seller was maker, so it's a "SELL" trade in logic
        }
    }

    mock_ws = MockWebSocket([trade_message])
    mock_connect = MockConnect(mock_ws)

    with patch("websockets.connect", return_value=mock_connect):
        with pytest.raises(asyncio.CancelledError):
            await binance_websocket_consumer(mock_redis)

    # Assert Redis calls
    assert mock_redis.publish.call_count >= 1
    # Check that a tick was published correctly
    publish_calls = mock_redis.publish.call_args_list
    tick_str = publish_calls[0][0][1] # First call, first positional arg is channel, second is message
    tick = json.loads(tick_str)

    assert tick["symbol"] == "BTCUSDT"
    assert tick["type"] == "trade"
    assert tick["price"] == 50000.0
    assert tick["qty"] == 0.1
    assert tick["side"] == "SELL"

@pytest.mark.asyncio
async def test_binance_websocket_consumer_book_ticker():
    mock_redis = AsyncMock()

    # Simulate a bookTicker message
    ticker_message = {
        "stream": "ethusdt@bookTicker",
        "data": {
            "s": "ETHUSDT",
            "E": 1623456789000,
            "b": "3000.00",
            "B": "1.5",
            "a": "3001.00",
            "A": "2.0"
        }
    }

    mock_ws = MockWebSocket([ticker_message])
    mock_connect = MockConnect(mock_ws)

    with patch("websockets.connect", return_value=mock_connect):
        with pytest.raises(asyncio.CancelledError):
            await binance_websocket_consumer(mock_redis)

    # Check that a tick was published correctly
    publish_calls = mock_redis.publish.call_args_list
    tick_str = publish_calls[0][0][1]
    tick = json.loads(tick_str)

    assert tick["symbol"] == "ETHUSDT"
    assert tick["type"] == "bookTicker"
    assert tick["bid_price"] == 3000.0
    assert tick["bid_qty"] == 1.5
    assert tick["ask_price"] == 3001.0
    assert tick["ask_qty"] == 2.0


@pytest.mark.asyncio
async def test_binance_websocket_consumer_depth():
    mock_redis = AsyncMock()

    # Simulate a depth message
    depth_message = {
        "stream": "solusdt@depth20@100ms",
        "data": {
            "s": "SOLUSDT",
            "E": 1623456789000,
            "bids": [["20.00", "100.0"]],
            "asks": [["20.10", "50.0"]]
        }
    }

    mock_ws = MockWebSocket([depth_message])
    mock_connect = MockConnect(mock_ws)

    with patch("websockets.connect", return_value=mock_connect):
        with pytest.raises(asyncio.CancelledError):
            await binance_websocket_consumer(mock_redis)

    # Check that a tick was published correctly
    publish_calls = mock_redis.publish.call_args_list
    tick_str = publish_calls[0][0][1]
    tick = json.loads(tick_str)

    assert tick["symbol"] == "SOLUSDT"
    assert tick["type"] == "depth"
    assert tick["bids"] == [["20.00", "100.0"]]
    assert tick["asks"] == [["20.10", "50.0"]]


@pytest.mark.asyncio
async def test_binance_websocket_consumer_empty_data_or_symbol():
    mock_redis = AsyncMock()

    # Simulate messages with missing required fields
    messages = [
        {"stream": "btcusdt@trade", "data": {}}, # No data
        {"stream": "btcusdt@trade", "data": {"p": "100"}}, # No symbol
    ]

    mock_ws = MockWebSocket(messages)
    mock_connect = MockConnect(mock_ws)

    with patch("websockets.connect", return_value=mock_connect):
        with pytest.raises(asyncio.CancelledError):
            await binance_websocket_consumer(mock_redis)

    # Ensure no ticks were published
    assert mock_redis.publish.call_count == 0


@pytest.mark.asyncio
@patch("asyncio.sleep")
async def test_binance_websocket_consumer_reconnect(mock_sleep):
    mock_redis = AsyncMock()

    # Simulate an error on the first recv, then CancelledError to exit
    mock_ws = MockWebSocket([], error_to_raise=Exception("Test connection error"))
    mock_connect = MockConnect(mock_ws)

    with patch("websockets.connect", return_value=mock_connect):
        with pytest.raises(asyncio.CancelledError):
            await binance_websocket_consumer(mock_redis)

    # Sleep should be called during exception backoff
    mock_sleep.assert_called_once_with(1)
