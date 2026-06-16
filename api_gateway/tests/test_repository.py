import pytest
import os
from api_gateway.repository import TimescaleTradeRepository

def test_timescale_repo_no_fallback_password(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_HOST", "test_host")
    monkeypatch.setenv("DB_PORT", "1234")
    monkeypatch.setenv("DB_NAME", "test_db")

    with pytest.raises(Exception):
        repo = TimescaleTradeRepository()

def test_timescale_repo_with_password(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "test_pass")
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_HOST", "test_host")
    monkeypatch.setenv("DB_PORT", "1234")
    monkeypatch.setenv("DB_NAME", "test_db")

    repo = TimescaleTradeRepository()
    assert "test_pass" in repo.dsn
