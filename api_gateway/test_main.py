import unittest
from unittest.mock import patch, MagicMock
import os
import urllib.request
import urllib.parse
from main import send_telegram_alert

class TestSendTelegramAlert(unittest.TestCase):
    @patch('os.getenv')
    @patch('urllib.request.urlopen')
    def test_missing_credentials(self, mock_urlopen, mock_getenv):
        # Missing both
        mock_getenv.return_value = None
        send_telegram_alert("test")
        mock_urlopen.assert_not_called()

        # Missing chat_id
        mock_getenv.side_effect = lambda key, default=None: "token" if key == "TELEGRAM_BOT_TOKEN" else None
        send_telegram_alert("test")
        mock_urlopen.assert_not_called()

        # Missing token
        mock_getenv.side_effect = lambda key, default=None: "chat" if key == "TELEGRAM_CHAT_ID" else None
        send_telegram_alert("test")
        mock_urlopen.assert_not_called()

    @patch('os.getenv')
    @patch('urllib.request.urlopen')
    def test_default_token(self, mock_urlopen, mock_getenv):
        def mock_env(key, default=None):
            if key == "TELEGRAM_BOT_TOKEN":
                return "your_telegram_token"
            elif key == "TELEGRAM_CHAT_ID":
                return "12345"
            return None
        mock_getenv.side_effect = mock_env
        send_telegram_alert("test")
        mock_urlopen.assert_not_called()

    @patch('os.getenv')
    @patch('urllib.request.urlopen')
    @patch('urllib.request.Request')
    def test_successful_alert(self, mock_request, mock_urlopen, mock_getenv):
        def mock_env(key, default=None):
            if key == "TELEGRAM_BOT_TOKEN":
                return "valid_token"
            elif key == "TELEGRAM_CHAT_ID":
                return "12345"
            return None
        mock_getenv.side_effect = mock_env

        mock_req_instance = MagicMock()
        mock_request.return_value = mock_req_instance

        send_telegram_alert("Hello World")

        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "https://api.telegram.org/botvalid_token/sendMessage")

        # Check data
        expected_data = urllib.parse.urlencode({'chat_id': '12345', 'text': 'Hello World'}).encode('utf-8')
        self.assertEqual(kwargs['data'], expected_data)

        mock_urlopen.assert_called_once_with(mock_req_instance, timeout=5)

    @patch('os.getenv')
    @patch('urllib.request.urlopen')
    @patch('builtins.print')
    def test_exception_handling(self, mock_print, mock_urlopen, mock_getenv):
        def mock_env(key, default=None):
            if key == "TELEGRAM_BOT_TOKEN":
                return "valid_token"
            elif key == "TELEGRAM_CHAT_ID":
                return "12345"
            return None
        mock_getenv.side_effect = mock_env

        mock_urlopen.side_effect = Exception("Network Error")

        # Should not raise exception
        send_telegram_alert("test")

        mock_print.assert_called_once()
        self.assertTrue("Failed to send telegram alert" in mock_print.call_args[0][0])

if __name__ == '__main__':
    unittest.main()
