import pytest
import asyncio
from unittest.mock import AsyncMock, patch
import logging

from data_ingestion.main import binance_websocket_consumer

@pytest.mark.asyncio
async def test_websocket_error_handling_and_backoff(caplog):
    caplog.set_level(logging.ERROR)

    mock_redis = AsyncMock()

    with patch("data_ingestion.main.websockets.connect") as mock_connect, \
         patch("data_ingestion.main.asyncio.sleep") as mock_sleep:

        # Simulate an exception, then cancel the loop to prevent it from hanging indefinitely
        mock_connect.side_effect = [Exception("Simulated connection error"), asyncio.CancelledError()]

        try:
            await binance_websocket_consumer(mock_redis)
        except asyncio.CancelledError:
            pass

        # Verify logging and backoff behavior
        assert "WebSocket error: Simulated connection error. Reconnecting in 1s..." in caplog.text
        mock_sleep.assert_called_once_with(1)
