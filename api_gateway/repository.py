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

        order = trade_data.get("original_signal", {})
        if not order:
            order = trade_data
            
        symbol = trade_data.get("symbol", order.get("symbol", "UNKNOWN"))
        side = trade_data.get("side", order.get("type", "UNKNOWN"))
        price = trade_data.get("executed_price", order.get("price", 0))
        qty = trade_data.get("quantity", order.get("quantity", 0))
        strategy = order.get("strategy", "System")

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trades (time, id, symbol, side, entry_price, exit_price, quantity, pnl_usdt, pnl_pct, open_time, close_time, strategy_name, stop_loss_price, take_profit_price, close_reason) VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
                str(trade_data.get("id", "mock-1")),
                symbol.upper(),
                side,
                float(trade_data.get("entry_price", 0)),
                float(trade_data.get("exit_price", 0)),
                float(qty),
                float(trade_data.get("pnl_usdt", 0)),
                float(trade_data.get("pnl_pct", 0)),
                trade_data.get("open_time_dt", "2026-01-01 00:00:00"), # Would be properly converted
                trade_data.get("close_time_dt", "2026-01-01 00:00:00"),
                strategy,
                float(trade_data.get("stop_loss_price", 0)),
                float(trade_data.get("take_profit_price", 0)),
                trade_data.get("close_reason", "UNKNOWN")
            )

    async def get_performance_summary(self) -> Dict[str, Any]:
        if not self.pool:
            return {
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "total_trades": 0
            }
            
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    SUM(pnl_usdt) as total_pnl,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*), 0) as win_rate
                FROM trades
            """)
            
            return {
                "total_pnl": float(row["total_pnl"] or 0),
                "win_rate": float(row["win_rate"] or 0) * 100,
                "max_drawdown": 0.0, # Complex calculation usually done via TimescaleDB window functions or pre-agg
                "sharpe_ratio": 0.0,
                "total_trades": int(row["total_trades"] or 0)
            }
