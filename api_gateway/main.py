from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio
import json
import os
import redis.asyncio as redis
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("APIGateway")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="CryptoScalper Pro API Gateway")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
from binance_client import BinanceClient

trade_repo = TimescaleTradeRepository()
binance_client = BinanceClient()

background_tasks = set()

# (Skipping down to add endpoint)

import subprocess

@app.on_event("startup")
async def startup_event():
    # Run Alembic migrations using subprocess to avoid asyncio loop conflicts
    try:
        logger.info("Running Database Migrations...")
        result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Database Migrations completed successfully.")
        else:
            logger.error(f"Alembic migration failed: {result.stderr}")
    except Exception as e:
        logger.error(f"Failed to run Alembic migrations: {e}")

    # Connect to TimescaleDB
    await trade_repo.connect()
    # Start Redis listener in background
    task = asyncio.create_task(redis_listener())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    
    # Start portfolio broadcaster
    ptask = asyncio.create_task(portfolio_broadcaster())
    background_tasks.add(ptask)
    ptask.add_done_callback(background_tasks.discard)

async def portfolio_broadcaster():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    while True:
        try:
            await asyncio.sleep(1)
            if not manager.active_connections:
                continue

            ledger_str = await redis_client.get("portfolio:ledger")
            if not ledger_str:
                continue
                
            ledger = json.loads(ledger_str)
            starting_capital = float(await redis_client.get("portfolio:starting_capital") or ledger.get("USDT", 0.0))
            
            usdt_balance = ledger.get("USDT", 0.0)
            total_crypto_value = 0.0
            holdings = []

            for asset, qty in ledger.items():
                if asset == "USDT" or qty == 0:
                    continue
                
                # Fetch latest price
                tick_str = await redis_client.get(f"ticks:{asset}USDT")
                price = 0.0
                if tick_str:
                    tick = json.loads(tick_str)
                    price = float(tick.get("price", 0.0))
                
                value = price * qty
                total_crypto_value += value
                holdings.append({
                    "symbol": asset,
                    "quantity": qty,
                    "current_price": price,
                    "value": value
                })

            total_capital = usdt_balance + total_crypto_value
            net_profit = total_capital - starting_capital

            payload = {
                "total_capital": total_capital,
                "net_profit": net_profit,
                "usdt_balance": usdt_balance,
                "holdings": holdings
            }
            
            ws_msg = json.dumps({"channel": "portfolio", "data": payload})
            await manager.broadcast(ws_msg)
        except Exception as e:
            print(f"Portfolio broadcaster error: {e}")
            await asyncio.sleep(3)

async def redis_listener():
    while True:
        try:
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
        except Exception as e:
            print(f"Redis listener crashed: {e}. Reconnecting in 3 seconds...")
            await asyncio.sleep(3)

from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import Depends, HTTPException, status
import jwt
from datetime import datetime, timedelta

SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return username
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

@app.post("/api/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Hardcoded admin for MVP
    if form_data.username == "admin" and form_data.password == os.getenv("ADMIN_PASSWORD", "admin"):
        access_token = create_access_token(data={"sub": form_data.username}, expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        return {"access_token": access_token, "token_type": "bearer"}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

@app.get("/")
def read_root():
    return {"status": "ok", "service": "CryptoScalper API Gateway"}

@app.get("/api/portfolio/real")
async def get_real_portfolio(user: str = Depends(get_current_user)):
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        balances = await binance_client.get_real_portfolio()
        if isinstance(balances, dict) and "error" in balances:
            return {"status": "error", "message": balances["error"]}
            
        total_value_usdt = 0.0
        enriched_balances = []
        for b in balances:
            asset = b["asset"]
            free = float(b["free"])
            locked = float(b["locked"])
            total_qty = free + locked
            
            value_usdt = 0.0
            price = 1.0
            if asset != "USDT":
                tick_str = await redis_client.get(f"ticks:{asset}USDT")
                if tick_str:
                    tick = json.loads(tick_str)
                    price = float(tick.get("price", 0.0))
                value_usdt = total_qty * price
            else:
                value_usdt = total_qty
                
            total_value_usdt += value_usdt
            enriched_balances.append({
                "asset": asset,
                "free": free,
                "locked": locked,
                "total": total_qty,
                "price_usdt": price if asset != "USDT" else 1.0,
                "value_usdt": value_usdt
            })
            
        return {
            "status": "ok",
            "total_value_usdt": total_value_usdt,
            "balances": sorted(enriched_balances, key=lambda x: x["value_usdt"], reverse=True)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/health")
@limiter.limit("10/minute")
async def health_check(request: Request):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    status = {"status": "ok", "redis": "ok", "db": "ok", "ingestion": "ok", "circuit_breaker": "closed"}
    
    try:
        await redis_client.ping()
    except Exception:
        status["redis"] = "error"
        status["status"] = "error"
        
    try:
        hb_str = await redis_client.get("ingestion:heartbeat")
        if hb_str:
            age = (asyncio.get_event_loop().time() * 1000) - int(hb_str)
            if age > 60000: # 60 seconds
                status["ingestion"] = "stale"
                status["status"] = "error"
    except Exception:
        pass
        
    cb_state = await redis_client.hgetall("risk:circuit_breaker")
    if cb_state and cb_state.get("status") == "open":
        status["circuit_breaker"] = "open"
        
    return status

@app.get("/api/symbols")
async def get_symbols(user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
    results = []
    for s in symbols:
        tick_str = await redis_client.get(f"tick:last:{s}")
        if tick_str:
            tick = json.loads(tick_str)
            results.append({"symbol": s, "price": tick.get("price")})
        else:
            results.append({"symbol": s, "price": None})
    return {"status": "ok", "symbols": results}

@app.get("/api/trades")
async def get_trades(limit: int = 50, user: str = Depends(get_current_user)):
    trades = await trade_repo.get_recent_trades(limit=limit)
    return {"status": "ok", "trades": trades}

@app.get("/api/trades/{symbol}")
async def get_trades_by_symbol(symbol: str, limit: int = 50, user: str = Depends(get_current_user)):
    trades = await trade_repo.get_trades_by_symbol(symbol=symbol.upper(), limit=limit)
    return {"status": "ok", "trades": trades}

@app.get("/api/performance/summary")
async def get_performance_summary(user: str = Depends(get_current_user)):
    summary = await trade_repo.get_performance_summary()
    summary["status"] = "ok"
    return summary

@app.get("/api/performance/daily")
async def get_performance_daily(user: str = Depends(get_current_user)):
    return {"status": "ok", "daily": []}

@app.get("/api/config")
async def get_config(user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    cfg_str = await redis_client.get("config:risk")
    if cfg_str:
        return json.loads(cfg_str)
    return {
        "MAX_OPEN_POSITIONS": int(os.getenv("MAX_OPEN_POSITIONS", 3)),
        "MAX_EXPOSURE_PER_SYMBOL_USDT": float(os.getenv("MAX_EXPOSURE_PER_SYMBOL_USDT", 1000.0)),
        "MAX_DAILY_LOSS_USDT": float(os.getenv("MAX_DAILY_LOSS_USDT", 50.0))
    }

@app.put("/api/config")
async def update_config(config: dict, user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await redis_client.set("config:risk", json.dumps(config))
    # In a real scenario we might broadcast a RELOAD_CONFIG command
    await manager.broadcast(json.dumps({"channel": "system:commands", "data": "RELOAD_CONFIG"}))
    return {"status": "ok"}

@app.post("/api/bot/start")
async def start_bot(user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await redis_client.set("bot:status", "running")
    await manager.broadcast(json.dumps({"channel": "system", "data": {"bot_status": "running"}}))
    return {"status": "ok"}

@app.post("/api/bot/stop")
async def stop_bot(user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await redis_client.set("bot:status", "stopped")
    await manager.broadcast(json.dumps({"channel": "system", "data": {"bot_status": "stopped"}}))
    return {"status": "ok"}

@app.get("/api/bot/status")
async def bot_status(user: str = Depends(get_current_user)):
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    status_str = await redis_client.get("bot:status")
    cb_state = await redis_client.hgetall("risk:circuit_breaker")
    balance = await redis_client.get("paper:balance")
    
    return {
        "status": status_str or "stopped",
        "circuit_breaker": cb_state if cb_state else {"status": "closed"},
        "paper_balance": float(balance) if balance else float(os.getenv("STARTING_CAPITAL", 100.0))
    }

@app.post("/api/bot/toggle")
async def toggle_bot(enabled: bool, user: str = Depends(get_current_user)):
    # Legacy endpoint mapping
    if enabled:
        return await start_bot(user)
    else:
        return await stop_bot(user)

@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    # In a real app we would pass token in query params or first message to authenticate WS
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

