from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_kill_switch_no_auth():
    response = client.post("/api/kill-switch")
    assert response.status_code == 401

def test_kill_switch_with_auth():
    response = client.post("/api/kill-switch", headers={"Authorization": "Bearer super_secret_jwt_key"})
    assert response.status_code == 200
