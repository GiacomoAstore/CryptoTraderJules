import asyncio
import json
import logging
import os
import redis.asyncio as redis
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DataIngestion")

SYMBOLS = ["btcusdt", "ethusdt", "bnbusdt", "solusdt", "xrpusdt"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

async def binance_websocket_consumer(redis_client):
    uri = f"wss://stream.binance.com:9443/stream?streams={'@ticker/'.join(SYMBOLS)}@ticker"
    logger.info(f"Connecting to Binance WS: {uri}")

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                logger.info("Connected to Binance WebSocket.")
                while True:
                    message = await websocket.recv()
                    payload = json.loads(message)
                    data = payload.get("data", {})
                    if not data:
                        continue
                    # Normalize tick (simplified)
                    tick = {
                        "symbol": data.get("s"),
                        "price": float(data.get("c", 0)),
                        "timestamp": data.get("E")
                    }
                    if tick["symbol"]:
                        await redis_client.publish(f"ticks:{tick['symbol']}", json.dumps(tick))
        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await binance_websocket_consumer(redis_client)

if __name__ == "__main__":
    asyncio.run(main())
