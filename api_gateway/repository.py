from abc import ABC, abstractmethod
from typing import List, Dict, Any
import asyncpg
import os

class TradeRepository(ABC):
    @abstractmethod
    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def insert_trade(self, trade_data: Dict[str, Any]):
        pass

class TimescaleTradeRepository(TradeRepository):
    def __init__(self):
        self.pool = None
        self.dsn = f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:{os.getenv('DB_PASSWORD', 'crypto_pass')}@{os.getenv('DB_HOST', 'timescaledb')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'cryptoscalper_db')}"

    async def connect(self):
        if not self.pool:
            try:
                self.pool = await asyncpg.create_pool(dsn=self.dsn)
            except Exception as e:
                print(f"Warning: Failed to connect to TimescaleDB ({e}). Mocking data.")
                self.pool = None

    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.pool:
            return [{"id": "mock-1", "symbol": "BTCUSDT", "side": "BUY", "price": 65000.0, "quantity": 0.01}]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades ORDER BY time DESC LIMIT $1", limit)
            return [dict(row) for row in rows]

    async def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.pool:
            return [{"id": "mock-1", "symbol": symbol, "side": "BUY", "price": 65000.0, "quantity": 0.01}]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades WHERE symbol = $1 ORDER BY time DESC LIMIT $2", symbol, limit)
            return [dict(row) for row in rows]

    async def insert_trade(self, trade_data: Dict[str, Any]):
        if not self.pool:
            return

        # Parse the execution result (e.g. {"status": "FILLED", "order": {"type": "BUY", "symbol": "btcusdt", "price": 65000, "strategy": "EMA Crossover"}})
        order = trade_data.get("order", {})
        if not order:
            return

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trades (time, trade_id, symbol, side, price, quantity, strategy) VALUES (NOW(), $1, $2, $3, $4, $5, $6)",
                f"mock-{order.get('price')}", # dummy trade id for mock
                order.get("symbol", "").upper(),
                order.get("type", "BUY"),
                float(order.get("price", 0)),
                float(order.get("quantity", 0.01)),
                order.get("strategy", "Manual")
            )
