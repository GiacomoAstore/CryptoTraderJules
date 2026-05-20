import asyncio
import json
import logging
import os
import time
from typing import TypedDict, Optional
from collections import defaultdict
import redis.asyncio as redis
import websockets
from pythonjsonlogger import jsonlogger

from tick_writer import TickWriter

# Setup structured logging
logger = logging.getLogger("DataIngestion")
logger.setLevel(logging.INFO)
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)

SYMBOLS = [s.strip().lower() for s in os.getenv("WATCHED_SYMBOLS", "btcusdt,ethusdt,bnbusdt,solusdt,xrpusdt,adausdt,dogeusdt,shibusdt,avaxusdt,dotusdt,linkusdt,trxusdt,ltcusdt,bchusdt,uniusdt,xlmusdt,nearusdt,atomusdt,aptusdt").split(",")]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class NormalizedTick(TypedDict):
    symbol: str
    price: float
    qty: float
    side: str
    timestamp_ms: int
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float

# Local state to hold the latest bid/ask and price
state = defaultdict(lambda: {
    "price": 0.0,
    "qty": 0.0,
    "side": "UNKNOWN",
    "bid_price": 0.0,
    "ask_price": 0.0,
    "bid_qty": 0.0,
    "ask_qty": 0.0,
    "timestamp_ms": 0
})

async def heartbeat_publisher(redis_client):
    """Publishes a heartbeat to Redis every 5 seconds for health monitoring."""
    while True:
        try:
            await redis_client.set("ingestion:heartbeat", int(time.time() * 1000))
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Heartbeat publisher error", extra={"error": str(e)})
            await asyncio.sleep(2)


tick_writer: TickWriter | None = None


async def publish_tick(redis_client, symbol: str):
    s = state[symbol]
    if s["price"] <= 0 or s["bid_price"] <= 0 or s["ask_price"] <= 0:
        return
    tick: NormalizedTick = {
        "symbol": symbol.upper(),
        "price": s["price"],
        "qty": s["qty"],
        "side": s["side"],
        "timestamp_ms": s["timestamp_ms"],
        "bid_price": s["bid_price"],
        "ask_price": s["ask_price"],
        "bid_qty": s["bid_qty"],
        "ask_qty": s["ask_qty"]
    }
    tick_json = json.dumps(tick)
    
    # Cache the last tick with TTL 10 seconds
    await redis_client.setex(f"tick:last:{symbol.upper()}", 10, tick_json)
    # Publish to Pub/Sub
    await redis_client.publish(f"ticks:{symbol.upper()}", tick_json)

    if tick_writer:
        await tick_writer.enqueue(tick)

async def binance_websocket_consumer(redis_client):
    streams = []
    for s in SYMBOLS:
        streams.append(f"{s}@trade")
        streams.append(f"{s}@depth20@100ms")
        streams.append(f"{s}@bookTicker")
    
    stream_param = '/'.join(streams)
    uri = f"wss://stream.binance.com:9443/stream?streams={stream_param}"
    
    backoff = 1
    max_backoff = 30

    while True:
        logger.info("Connecting to Binance WS", extra={"uri": uri})
        try:
            async with websockets.connect(uri) as websocket:
                logger.info("Connected to Binance WebSocket.")
                backoff = 1 # reset backoff on successful connection
                
                while True:
                    message = await websocket.recv()
                    payload = json.loads(message)
                    stream_name = payload.get("stream", "")
                    data = payload.get("data", {})
                    
                    if not data:
                        continue
                        
                    symbol = data.get("s", "").lower()
                    if not symbol:
                        continue

                    if "@trade" in stream_name:
                        state[symbol]["price"] = float(data.get("p", 0))
                        state[symbol]["qty"] = float(data.get("q", 0))
                        state[symbol]["side"] = "SELL" if data.get("m") else "BUY"
                        state[symbol]["timestamp_ms"] = data.get("E", int(time.time() * 1000))
                        await publish_tick(redis_client, symbol)
                        
                    elif "@bookTicker" in stream_name:
                        bid = float(data.get("b", 0))
                        ask = float(data.get("a", 0))
                        state[symbol]["bid_price"] = bid
                        state[symbol]["bid_qty"] = float(data.get("B", 0))
                        state[symbol]["ask_price"] = ask
                        state[symbol]["ask_qty"] = float(data.get("A", 0))
                        state[symbol]["timestamp_ms"] = int(time.time() * 1000)
                        if bid > 0 and ask > 0:
                            mid = (bid + ask) / 2
                            if state[symbol]["price"] <= 0:
                                state[symbol]["price"] = mid
                        await publish_tick(redis_client, symbol)

                    elif "@depth20" in stream_name:
                        # Orderbook level 20 for full snapshot if needed by other services
                        depth = {
                            "symbol": symbol.upper(),
                            "bids": [[float(p), float(q)] for p, q in data.get("b", [])],
                            "asks": [[float(p), float(q)] for p, q in data.get("a", [])],
                            "timestamp": data.get("E", int(time.time() * 1000))
                        }
                        depth_json = json.dumps(depth)
                        await redis_client.setex(f"orderbook:{symbol.upper()}", 10, depth_json)
                        await redis_client.publish(f"orderbook:{symbol.upper()}", depth_json)

        except Exception as e:
            logger.error("WebSocket connection lost", extra={"error": str(e), "backoff_seconds": backoff})
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)

async def main():
    global tick_writer
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    tick_writer = TickWriter()
    try:
        await tick_writer.start()
    except Exception as e:
        logger.error("TickWriter failed to start — ingestion continues without DB persist", extra={"error": str(e)})
        tick_writer = None

    # Start heartbeat task
    asyncio.create_task(heartbeat_publisher(redis_client))

    try:
        await binance_websocket_consumer(redis_client)
    finally:
        if tick_writer:
            await tick_writer.stop()

if __name__ == "__main__":
    asyncio.run(main())
