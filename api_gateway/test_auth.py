from fastapi.testclient import TestClient
import sys
import os

# Ensure the parent directory is in path so absolute imports work
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from main import app

client = TestClient(app)

def test_api_metrics_no_auth():
    response = client.get("/api/metrics")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API Key"

def test_api_metrics_with_invalid_auth():
    response = client.get("/api/metrics", headers={"X-API-Key": "wrong_key"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API Key"

def test_api_metrics_with_valid_auth():
    # We might mock trade_repo and redis or rely on them failing gracefully as they currently do in main.py exceptions block
    response = client.get("/api/metrics", headers={"X-API-Key": "dev_secret_key"})
    assert response.status_code == 200
    assert "metrics" in response.json()

def test_api_kill_switch_no_auth():
    response = client.post("/api/kill-switch")
    assert response.status_code == 401
