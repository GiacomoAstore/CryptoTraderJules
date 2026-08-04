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

# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------

def binance_to_cryptocom_symbol(symbol: str) -> str:
    """Convert Binance-style 'BTCUSDT' to Crypto.com-style 'BTC_USDT'."""
    symbol = symbol.upper()
    if "_" in symbol:
        return symbol
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "CRO"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}_{quote}"
    return f"{symbol[:-4]}_{symbol[-4:]}" if len(symbol) > 4 else symbol


def cryptocom_to_internal_symbol(instrument_name: str) -> str:
    """Convert Crypto.com 'BTC_USDT' to internal key 'btcusdt'."""
    return instrument_name.replace("_", "").lower()


def get_active_instruments() -> set[str]:
    """Fetch active instrument names from Crypto.com Exchange API using built-in urllib."""
    import urllib.request
    import urllib.error
    url = "https://api.crypto.com/exchange/v1/public/get-instruments"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            if resp_data.get("code") == 0:
                instruments = resp_data.get("result", {}).get("data", [])
                return {inst["symbol"].upper() for inst in instruments}
    except Exception as e:
        logger.error("Failed to fetch active instruments from REST API", extra={"error": str(e)})
    return set()



# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_WATCHED_SYMBOLS_RAW = os.getenv(
    "WATCHED_SYMBOLS",
    "btcusdt,ethusdt,solusdt,xrpusdt,adausdt,dogeusdt,avaxusdt,dotusdt,linkusdt,trxusdt,ltcusdt,bchusdt,uniusdt,xlmusdt,nearusdt,atomusdt,aptusdt",
)
SYMBOLS = [
    s.strip().lower()
    for s in _WATCHED_SYMBOLS_RAW.split(",")
    if s.strip() and s.strip().lower() != "utkusdt"
]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Crypto.com Exchange WebSocket URLs
WS_MARKET_URL = os.getenv(
    "CRYPTOCOM_WS_MARKET_URL",
    "wss://stream.crypto.com/exchange/v1/market",
)

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
    if s["price"] <= 0:
        if s["bid_price"] > 0 and s["ask_price"] > 0:
            s["price"] = (s["bid_price"] + s["ask_price"]) / 2.0
        else:
            return
    if s["bid_price"] <= 0:
        s["bid_price"] = s["price"]
    if s["ask_price"] <= 0:
        s["ask_price"] = s["price"]
    tick: NormalizedTick = {
        "symbol": symbol.upper(),
        "price": s["price"],
        "qty": s["qty"],
        "side": s["side"],
        "timestamp_ms": s["timestamp_ms"] or int(time.time() * 1000),
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


async def cryptocom_websocket_consumer(redis_client):
    """Connect to Crypto.com Exchange WebSocket and consume market data.

    Subscribes to:
      - trade.{instrument} — individual trades
      - ticker.{instrument} — best bid/ask (replaces Binance bookTicker)
      - book.{instrument}.20 — order book depth 20
    """
    # Fetch active instruments first
    active_instruments = get_active_instruments()
    logger.info("Fetched active instruments from exchange", extra={"count": len(active_instruments)})

    # Build Crypto.com instrument names from WATCHED_SYMBOLS and filter by active instruments
    instruments = []
    for s in SYMBOLS:
        inst = binance_to_cryptocom_symbol(s)
        if not active_instruments or inst.upper() in active_instruments:
            instruments.append(inst)
        else:
            logger.warning("Symbol not available on Crypto.com Exchange — filtered out", extra={"symbol": s, "mapped": inst})

    channels = []
    for inst in instruments:
        channels.append(f"trade.{inst}")
        channels.append(f"ticker.{inst}")
        channels.append(f"book.{inst}.10")

    backoff = 1
    max_backoff = 30

    while True:
        logger.info("Connecting to Crypto.com WS", extra={"uri": WS_MARKET_URL})
        try:
            async with websockets.connect(WS_MARKET_URL) as websocket:
                logger.info("Connected to Crypto.com WebSocket.")
                backoff = 1  # reset backoff on successful connection

                # Wait 1 second before subscribing (recommended by Crypto.com docs)
                await asyncio.sleep(1)

                # Subscribe to all channels
                subscribe_msg = json.dumps({
                    "id": 1,
                    "method": "subscribe",
                    "params": {"channels": channels},
                    "nonce": int(time.time() * 1000),
                })
                await websocket.send(subscribe_msg)
                logger.info("Subscribed to channels", extra={"count": len(channels)})

                while True:
                    message = await websocket.recv()
                    payload = json.loads(message)

                    method = payload.get("method", "")

                    if not hasattr(cryptocom_websocket_consumer, "_msg_count"):
                        cryptocom_websocket_consumer._msg_count = 0
                    if cryptocom_websocket_consumer._msg_count < 10:
                        cryptocom_websocket_consumer._msg_count += 1
                        logger.info("Raw WS message sample", extra={"payload": payload})

                    # Handle heartbeat — Crypto.com sends heartbeat every 30s
                    if method == "public/heartbeat":
                        heartbeat_response = json.dumps({
                            "id": payload.get("id", int(time.time() * 1000)),
                            "method": "public/respond-heartbeat",
                        })
                        await websocket.send(heartbeat_response)
                        continue

                    # Skip subscribe error confirmations (process if result is present)
                    if method == "subscribe" and "result" not in payload:
                        code = payload.get("code", 0)
                        if code != 0:
                            logger.error("Subscribe failed", extra={"payload": payload})
                        continue

                    # Process market data
                    result = payload.get("result", {})
                    channel = result.get("channel", "")
                    data_list = result.get("data", [])
                    instrument_name = result.get("instrument_name", "")

                    if not channel or not instrument_name:
                        continue

                    # Convert Crypto.com instrument name to internal key
                    symbol = cryptocom_to_internal_symbol(instrument_name)

                    if channel == "trade" or channel.startswith("trade.") or channel.startswith("trade"):
                        # Trade data: list of trades
                        for trade in data_list:
                            state[symbol]["price"] = float(trade.get("p", 0))
                            state[symbol]["qty"] = float(trade.get("q", 0))
                            state[symbol]["side"] = trade.get("s", "UNKNOWN").upper()
                            state[symbol]["timestamp_ms"] = int(
                                trade.get("t", int(time.time() * 1000))
                            )
                        await publish_tick(redis_client, symbol)

                    elif channel == "ticker" or channel.startswith("ticker.") or channel.startswith("ticker"):
                        # Ticker data: best bid/ask (replaces Binance bookTicker)
                        for tick_data in data_list:
                            bid = float(tick_data.get("b", 0))
                            ask = float(tick_data.get("k", 0))
                            bid_qty = float(tick_data.get("bs", 0))
                            ask_qty = float(tick_data.get("ks", 0))
                            state[symbol]["bid_price"] = bid
                            state[symbol]["ask_price"] = ask
                            state[symbol]["bid_qty"] = bid_qty
                            state[symbol]["ask_qty"] = ask_qty
                            state[symbol]["timestamp_ms"] = int(time.time() * 1000)
                            if bid > 0 and ask > 0:
                                mid = (bid + ask) / 2
                                if state[symbol]["price"] <= 0:
                                    state[symbol]["price"] = mid
                            # Use last trade price if available
                            last = float(tick_data.get("a", 0))
                            if last > 0:
                                state[symbol]["price"] = last
                        await publish_tick(redis_client, symbol)

                    elif channel.startswith("book."):
                        # Order book depth snapshot
                        for book_data in data_list:
                            bids = book_data.get("bids", [])
                            asks = book_data.get("asks", [])
                            depth = {
                                "symbol": symbol.upper(),
                                "bids": [[float(entry[0]), float(entry[1])] for entry in bids],
                                "asks": [[float(entry[0]), float(entry[1])] for entry in asks],
                                "timestamp": int(book_data.get("t", int(time.time() * 1000))),
                            }
                            depth_json = json.dumps(depth)
                            await redis_client.setex(
                                f"orderbook:{symbol.upper()}", 10, depth_json
                            )
                            await redis_client.publish(
                                f"orderbook:{symbol.upper()}", depth_json
                            )
                            # Update best bid/ask from book
                            if bids:
                                state[symbol]["bid_price"] = float(bids[0][0])
                                state[symbol]["bid_qty"] = float(bids[0][1])
                            if asks:
                                state[symbol]["ask_price"] = float(asks[0][0])
                                state[symbol]["ask_qty"] = float(asks[0][1])

        except Exception as e:
            logger.error(
                "WebSocket connection lost",
                extra={"error": str(e), "backoff_seconds": backoff},
            )
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
        await cryptocom_websocket_consumer(redis_client)
    finally:
        if tick_writer:
            await tick_writer.stop()

if __name__ == "__main__":
    asyncio.run(main())
