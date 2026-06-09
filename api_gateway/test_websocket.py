import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from main import app
import os

client = TestClient(app)

def test_websocket_missing_token():
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws"):
            pass
    assert excinfo.value.code == 1008

def test_websocket_invalid_token():
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws?token=invalid_token"):
            pass
    assert excinfo.value.code == 1008

def test_websocket_valid_token():
    import jwt
    from datetime import datetime, timedelta, timezone

    secret = os.getenv("JWT_SECRET", "super_secret_jwt_key")
    payload = {
        "sub": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24)
    }
    valid_token = jwt.encode(payload, secret, algorithm="HS256")

    # This should connect successfully without raising an exception
    with client.websocket_connect(f"/ws?token={valid_token}") as websocket:
        # We can try to send some text to verify it's open
        websocket.send_text("ping")
        # To make sure we don't hang, we'll just disconnect or let it close
