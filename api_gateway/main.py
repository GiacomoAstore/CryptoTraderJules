from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
import os
import redis.asyncio as redis
import urllib.request
import urllib.parse

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

from datetime import date
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def compute_daily_performance():
    print("Computing daily performance...")
    if not trade_repo.pool:
        print("DB pool not available for daily performance computation.")
        return

    try:
        async with trade_repo.pool.acquire() as conn:
            # Aggregate today's trades
            rows = await conn.fetch('''
                SELECT
                    COUNT(*) as total_trades,
                    SUM(pnl_netto) as total_pnl,
                    COUNT(CASE WHEN pnl_netto > 0 THEN 1 END) as winning_trades
                FROM trades
                WHERE time::date = CURRENT_DATE AND pnl_netto IS NOT NULL
            ''')

            if not rows or rows[0]['total_trades'] == 0:
                print("No trades today to compute performance.")
                return

            stats = rows[0]
            total_trades = stats['total_trades']
            total_pnl = stats['total_pnl'] or 0.0
            winning_trades = stats['winning_trades']

            win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0

            # Very basic max drawdown calculation (for simplicity, using worst single trade loss)
            # In a real scenario, this would compute the max peak-to-trough drop over a cumulative series.
            md_row = await conn.fetch('''
                SELECT MIN(pnl_netto) as max_loss FROM trades WHERE time::date = CURRENT_DATE AND pnl_netto IS NOT NULL
            ''')
            max_drawdown = md_row[0]['max_loss'] if md_row and md_row[0]['max_loss'] is not None and md_row[0]['max_loss'] < 0 else 0.0

            # Simple Sharpe ratio approximation: mean(PnL) / std(PnL)
            sharpe_row = await conn.fetch('''
                SELECT AVG(pnl_netto) as mean_pnl, STDDEV(pnl_netto) as std_pnl
                FROM trades WHERE time::date = CURRENT_DATE AND pnl_netto IS NOT NULL
            ''')

            mean_pnl = sharpe_row[0]['mean_pnl'] or 0.0
            std_pnl = sharpe_row[0]['std_pnl'] or 1.0 # avoid div by zero
            sharpe_ratio = mean_pnl / std_pnl if std_pnl > 0 else 0.0

            # Insert or update daily_performance
            today = date.today()
            await conn.execute('''
                INSERT INTO daily_performance (date, total_pnl, win_rate, sharpe_ratio, max_drawdown, total_trades)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (date) DO UPDATE SET
                    total_pnl = EXCLUDED.total_pnl,
                    win_rate = EXCLUDED.win_rate,
                    sharpe_ratio = EXCLUDED.sharpe_ratio,
                    max_drawdown = EXCLUDED.max_drawdown,
                    total_trades = EXCLUDED.total_trades
            ''', today, total_pnl, win_rate, sharpe_ratio, max_drawdown, total_trades)

            # Send alert
            msg = f"Daily Report ({today}):\nTrades: {total_trades}\nPnL: {total_pnl:.2f}\nWin Rate: {win_rate:.1f}%\nMax Drawdown: {max_drawdown:.2f}\nSharpe: {sharpe_ratio:.2f}"

            # publish to Redis so the listener can pick it up
            redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
            await redis_client.publish("alerts", json.dumps({"message": msg}))
            await redis_client.aclose()

            print(f"Daily performance updated: {msg}")
    except Exception as e:
        print(f"Failed to compute daily performance: {e}")

@app.on_event("startup")
async def startup_event():
    # Connect to TimescaleDB
    await trade_repo.connect()
    # Start Redis listener in background
    asyncio.create_task(redis_listener())

    # Schedule daily performance at 23:59
    scheduler.add_job(compute_daily_performance, 'cron', hour=23, minute=59)
    scheduler.start()

def send_telegram_alert(message: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id or bot_token == "your_telegram_token":
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Failed to send telegram alert: {e}")

async def redis_listener():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("executed_trades", "signals", "ticks:*", "alerts")

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

            if channel == "alerts":
                # Assuming data is a dict with a "message" key
                msg_text = data.get("message", str(data))
                asyncio.get_event_loop().run_in_executor(None, send_telegram_alert, msg_text)

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

@app.get("/api/metrics")
async def get_metrics():
    # Fetch paper balance from Redis
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        balance_raw = await redis_client.get("paper:balance")
        balance = float(balance_raw) if balance_raw else 10000.0
        await redis_client.aclose()
    except Exception as e:
        print(f"Failed to fetch balance: {e}")
        balance = 10000.0

    metrics = {
        "paper_balance": balance,
        "daily_pnl": 0.0,
        "win_rate": 0.0,
        "max_drawdown": 0.0
    }

    if trade_repo.pool:
        try:
            async with trade_repo.pool.acquire() as conn:
                # Fallback: compute live daily metrics instead of waiting for cron job
                rows = await conn.fetch('''
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(pnl_netto) as total_pnl,
                        COUNT(CASE WHEN pnl_netto > 0 THEN 1 END) as winning_trades,
                        MIN(pnl_netto) as max_loss
                    FROM trades
                    WHERE time::date = CURRENT_DATE AND pnl_netto IS NOT NULL
                ''')
                if rows and rows[0]['total_trades'] > 0:
                    stats = rows[0]
                    total_trades = stats['total_trades']
                    metrics["daily_pnl"] = stats['total_pnl'] or 0.0
                    metrics["win_rate"] = (stats['winning_trades'] / total_trades) * 100 if total_trades > 0 else 0.0
                    metrics["max_drawdown"] = stats['max_loss'] if stats['max_loss'] is not None and stats['max_loss'] < 0 else 0.0
        except Exception as e:
            print(f"Failed to fetch daily metrics: {e}")

    return {"status": "ok", "metrics": metrics}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Just keep the connection open, can receive commands from UI later
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
