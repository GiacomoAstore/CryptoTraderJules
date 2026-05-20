"""
Integration tests for Redis bot gating and risk-manager signal approval.

Unit tests only:
  pytest tests/ -m "not integration"

Full stack (docker compose up):
  INTEGRATION_STACK=1 pytest tests/integration/ -m integration
"""
import asyncio
import json
import os
import time

import pytest
import redis.asyncio as redis

pytestmark = pytest.mark.integration

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
STACK_ENABLED = os.getenv("INTEGRATION_STACK", "").lower() in ("1", "true", "yes")


@pytest.fixture
async def redis_client():
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis not available at {REDIS_HOST}:{REDIS_PORT} ({exc})")
    yield client
    await client.aclose()


async def _next_data_message(pubsub, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if message and message.get("type") == "message":
            return message
        await asyncio.sleep(0.1)
    return None


@pytest.mark.asyncio
async def test_bot_stopped_prevents_approval(redis_client):
    if not STACK_ENABLED:
        pytest.skip("Set INTEGRATION_STACK=1 with full docker compose running")

    await redis_client.set("bot:status", "stopped")
    await redis_client.delete("bot:safe_mode")
    await redis_client.hset("risk:circuit_breaker", "status", "closed")

    pubsub = redis_client.pubsub()
    await pubsub.subscribe("approved_orders")

    signal = {
        "type": "BUY",
        "symbol": "BTCUSDT",
        "price": "65000.0",
        "strength": "1.0",
        "strategy_name": "Consensus",
        "timestamp_ms": int(time.time() * 1000),
        "ab_variant": "A",
    }
    await redis_client.publish("signals:BTCUSDT", json.dumps(signal))

    message = await _next_data_message(pubsub, timeout=3.0)
    assert message is None, "Expected no approved order when bot is stopped"


@pytest.mark.asyncio
async def test_bot_running_approves_signal(redis_client):
    if not STACK_ENABLED:
        pytest.skip("Set INTEGRATION_STACK=1 with full docker compose running")

    await redis_client.set("bot:status", "running")
    await redis_client.delete("bot:safe_mode")
    await redis_client.hset("risk:circuit_breaker", "status", "closed")

    for i in range(15):
        tick = {
            "symbol": "BTCUSDT",
            "price": 65000.0 + i,
            "qty": 0.01,
            "side": "BUY",
            "timestamp_ms": int(time.time() * 1000),
        }
        await redis_client.publish("ticks:BTCUSDT", json.dumps(tick))
        await asyncio.sleep(0.05)

    pubsub = redis_client.pubsub()
    await pubsub.subscribe("approved_orders")

    signal = {
        "type": "BUY",
        "symbol": "BTCUSDT",
        "price": "65000.0",
        "strength": "1.0",
        "strategy_name": "Consensus",
        "timestamp_ms": int(time.time() * 1000),
        "ab_variant": "A",
    }
    await redis_client.publish("signals:BTCUSDT", json.dumps(signal))

    message = await _next_data_message(pubsub, timeout=5.0)
    assert message is not None, "Expected approved order when bot is running"
    payload = json.loads(message["data"])
    assert payload["symbol"] == "BTCUSDT"
    assert payload["type"] == "BUY"
    assert "command_id" in payload
