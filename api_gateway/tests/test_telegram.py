import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import api_gateway.main as main
import aiohttp

@pytest.mark.asyncio
async def test_send_telegram_alert():
    # Setup mock environment variables
    with patch("os.getenv", side_effect=lambda k: "dummy_token" if k == "TELEGRAM_BOT_TOKEN" else "dummy_chat"):
        # Create a mock aiohttp ClientSession
        mock_session = MagicMock(spec=aiohttp.ClientSession)
        mock_response = AsyncMock()
        mock_response.__aenter__.return_value = mock_response
        mock_session.post.return_value = mock_response

        main.http_client = mock_session

        await main.send_telegram_alert("Hello Test")

        # Verify it called post
        mock_session.post.assert_called_once_with(
            "https://api.telegram.org/botdummy_token/sendMessage",
            data={'chat_id': 'dummy_chat', 'text': 'Hello Test'},
            timeout=5
        )

@pytest.mark.asyncio
async def test_send_telegram_alert_no_tokens():
    # Without tokens it should return early
    with patch("os.getenv", return_value=None):
        main.http_client = MagicMock()
        await main.send_telegram_alert("Hello Test")
        main.http_client.post.assert_not_called()
