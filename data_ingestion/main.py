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
    # Combine streams for trade, order book depth, and best book ticker
    streams = []
    for s in SYMBOLS:
        streams.extend([f"{s}@trade", f"{s}@depth20@100ms", f"{s}@bookTicker"])
    uri = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

    backoff = 1

    while True:
        try:
            logger.info(f"Connecting to Binance WS: {uri}")
            async with websockets.connect(uri) as websocket:
                logger.info("Connected to Binance WebSocket.")
                backoff = 1 # Reset backoff on successful connect

                # Background task for heartbeat
                async def heartbeat():
                    while True:
                        await asyncio.sleep(30)
                        await redis_client.publish("ingestion:heartbeat", json.dumps({"status": "alive"}))

                heartbeat_task = asyncio.create_task(heartbeat())

                try:
                    while True:
                        message = await websocket.recv()
                        payload = json.loads(message)
                        stream = payload.get("stream", "")
                        data = payload.get("data", {})

                        if not data:
                            continue

                        # Basic normalized tick structure based on the stream type
                        symbol = data.get("s")
                        if not symbol:
                            continue

                        tick = {
                            "symbol": symbol,
                            "timestamp_ms": data.get("E", 0),
                        }

                        if "@trade" in stream:
                            tick.update({
                                "type": "trade",
                                "price": float(data.get("p", 0)),
                                "qty": float(data.get("q", 0)),
                                "side": "SELL" if data.get("m") else "BUY" # m=True means buyer is maker (so it was a sell order)
                            })
                        elif "@bookTicker" in stream:
                            tick.update({
                                "type": "bookTicker",
                                "bid_price": float(data.get("b", 0)),
                                "bid_qty": float(data.get("B", 0)),
                                "ask_price": float(data.get("a", 0)),
                                "ask_qty": float(data.get("A", 0))
                            })
                        elif "@depth" in stream:
                            tick.update({
                                "type": "depth",
                                "bids": data.get("bids", []),
                                "asks": data.get("asks", [])
                            })

                        # Publish and cache the last tick
                        tick_str = json.dumps(tick)
                        await redis_client.publish(f"ticks:{symbol}", tick_str)
                        await redis_client.setex(f"tick:last:{symbol}", 10, tick_str)

                finally:
                    heartbeat_task.cancel()

        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30) # Exponential backoff up to 30s

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await binance_websocket_consumer(redis_client)

if __name__ == "__main__":
    asyncio.run(main())
