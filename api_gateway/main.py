from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import os
import redis.asyncio as redis

app = FastAPI(title="CryptoScalper API Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

from repository import TimescaleTradeRepository

trade_repo = TimescaleTradeRepository()

@app.on_event("startup")
async def startup_event():
    # Connect to TimescaleDB
    await trade_repo.connect()
    # Start Redis listener in background
    asyncio.create_task(redis_listener())

async def redis_listener():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("executed_trades", "signals", "ticks:*")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            data = json.loads(message["data"])
            channel = message.get("channel", "")

            # Persist executed trades to DB
            if channel == "executed_trades":
                try:
                    await trade_repo.insert_trade(data)
                except Exception as e:
                    print(f"Failed to save trade to DB: {e}")

            ws_msg = json.dumps({"channel": channel, "data": data})
            await manager.broadcast(ws_msg)

@app.get("/")
def read_root():
    return {"status": "ok", "service": "CryptoScalper API Gateway"}

@app.get("/api/trades")
async def get_trades(limit: int = 50):
    trades = await trade_repo.get_recent_trades(limit=limit)
    return {"status": "ok", "trades": trades}

@app.get("/api/trades/{symbol}")
async def get_trades_by_symbol(symbol: str, limit: int = 50):
    trades = await trade_repo.get_trades_by_symbol(symbol=symbol.upper(), limit=limit)
    return {"status": "ok", "trades": trades}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Just keep the connection open, can receive commands from UI later
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
