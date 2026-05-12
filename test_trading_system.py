import asyncio
import json
import redis.asyncio as redis
import os
import time

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

async def test_system():
    print(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT} for testing...")
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        # Ensure connection works
        await redis_client.ping()
    except Exception as e:
        print(f"Failed to connect to redis: {e}")
        return

    print("1. Injecting mock market ticks...")
    # Inject a couple of ticks so paper trade engine has a 'last price' to reference
    mock_tick = {
        "type": "trade",
        "symbol": "BTCUSDT",
        "price": 65000.0,
        "qty": 0.05,
        "timestamp_ms": int(time.time() * 1000)
    }
    await redis_client.set("tick:last:BTCUSDT", json.dumps(mock_tick))
    await redis_client.publish("ticks:trade", json.dumps(mock_tick))

    await asyncio.sleep(1)

    print("2. Simulating a 'shadow' order from Signal Engine...")
    shadow_order = {
        "symbol": "BTCUSDT",
        "direction": "BUY",
        "suggested_price": 65000.0,
        "suggested_qty": 0.01,
        "strategy_name": "EMA Crossover Test",
        "timestamp_ms": int(time.time() * 1000)
    }
    await redis_client.publish("shadow_orders", json.dumps(shadow_order))

    await asyncio.sleep(1)

    print("3. Simulating an 'approved' real order from Risk Manager...")
    real_order = {
        "symbol": "BTCUSDT",
        "direction": "SELL",
        "suggested_price": 65000.0,
        "suggested_qty": 0.01,
        "strategy_name": "Momentum Burst Test",
        "timestamp_ms": int(time.time() * 1000)
    }
    await redis_client.publish("approved_orders", json.dumps(real_order))

    await asyncio.sleep(2)

    # 4. Read back the states
    print("4. Reading back system state from Redis...")

    open_pos = await redis_client.get("state:open_positions")
    shadow_pos = await redis_client.get("state:shadow_positions")
    live_pos = await redis_client.get("state:live_positions")
    paper_bal = await redis_client.get("paper:balance")

    print("--- SYSTEM STATE RESULTS ---")
    print(f"Paper Balance: {paper_bal}")
    print(f"Paper Positions: {open_pos}")
    print(f"Shadow Positions: {shadow_pos}")
    print(f"Live Positions: {live_pos}")

    print("\nTest completed successfully. Check logs of order_executor for Binance Live Execution attempt logs (it should fail if no API keys are set, or abort if hard cap is triggered).")

if __name__ == "__main__":
    asyncio.run(test_system())
