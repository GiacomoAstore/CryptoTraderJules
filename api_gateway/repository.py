from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import asyncpg
import os
import datetime

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

    @abstractmethod
    async def get_daily_performance_stats(self) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def save_daily_performance(self, date_val: datetime.date, stats: Dict[str, Any]):
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
                "INSERT INTO trades (time, trade_id, symbol, side, price, quantity, strategy, pnl_netto, gross_pnl, commission_paid, close_reason) VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                f"mock-{order.get('price')}", # dummy trade id for mock
                order.get("symbol", "").upper(),
                order.get("type", "BUY"),
                float(order.get("price", 0)),
                float(order.get("quantity", 0.01)),
                order.get("strategy", "Manual"),
                float(trade_data.get("pnl_netto", 0.0)) if "pnl_netto" in trade_data else None,
                float(trade_data.get("gross_pnl", 0.0)) if "gross_pnl" in trade_data else None,
                float(trade_data.get("commission_paid", 0.0)) if "commission_paid" in trade_data else None,
                trade_data.get("close_reason")
            )

    async def get_daily_performance_stats(self) -> Optional[Dict[str, Any]]:
        if not self.pool:
            return None

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT
                    COUNT(*) as total_trades,
                    SUM(pnl_netto) as total_pnl,
                    COUNT(CASE WHEN pnl_netto > 0 THEN 1 END) as winning_trades,
                    MIN(pnl_netto) as max_loss,
                    AVG(pnl_netto) as mean_pnl,
                    STDDEV(pnl_netto) as std_pnl
                FROM trades
                WHERE time::date = CURRENT_DATE AND pnl_netto IS NOT NULL
            ''')

            if not row or row['total_trades'] == 0:
                return {'total_trades': 0}

            total_trades = row['total_trades']
            total_pnl = row['total_pnl'] or 0.0
            winning_trades = row['winning_trades']

            win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
            max_drawdown = row['max_loss'] if row['max_loss'] is not None and row['max_loss'] < 0 else 0.0

            mean_pnl = row['mean_pnl'] or 0.0
            std_pnl = row['std_pnl'] or 1.0 # avoid div by zero
            sharpe_ratio = mean_pnl / std_pnl if std_pnl > 0 else 0.0

            return {
                'total_trades': total_trades,
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': sharpe_ratio
            }

    async def save_daily_performance(self, date_val: datetime.date, stats: Dict[str, Any]):
        if not self.pool or stats.get('total_trades', 0) == 0:
            return

        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO daily_performance (date, total_pnl, win_rate, sharpe_ratio, max_drawdown, total_trades)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (date) DO UPDATE SET
                    total_pnl = EXCLUDED.total_pnl,
                    win_rate = EXCLUDED.win_rate,
                    sharpe_ratio = EXCLUDED.sharpe_ratio,
                    max_drawdown = EXCLUDED.max_drawdown,
                    total_trades = EXCLUDED.total_trades
            ''', date_val, stats['total_pnl'], stats['win_rate'], stats['sharpe_ratio'], stats['max_drawdown'], stats['total_trades'])
