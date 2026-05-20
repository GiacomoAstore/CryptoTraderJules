from abc import ABC, abstractmethod
from typing import List, Dict, Any
import asyncpg
import logging
import os
import uuid

logger = logging.getLogger("APIGateway.Repository")

ALLOW_MOCK_TRADES = os.getenv("ALLOW_MOCK_TRADES", "false").lower() == "true"

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
    async def get_tables(self) -> List[str]:
        pass

    @abstractmethod
    async def query_table(self, table: str, limit: int = 100, filters: Dict[str, str] = None) -> List[Dict[str, Any]]:
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
                logger.error("Failed to connect to TimescaleDB: %s", e)
                self.pool = None

    def _unavailable_trades(self) -> List[Dict[str, Any]]:
        if ALLOW_MOCK_TRADES:
            logger.warning("Returning mock trade data (ALLOW_MOCK_TRADES=true)")
            return [{"id": "mock-1", "symbol": "BTCUSDT", "side": "BUY", "price": 65000.0, "quantity": 0.01}]
        return []

    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.pool:
            return self._unavailable_trades()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades ORDER BY time DESC LIMIT $1", limit)
            return [dict(row) for row in rows]

    async def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.pool:
            trades = self._unavailable_trades()
            if trades:
                trades[0]["symbol"] = symbol
            return trades

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades WHERE symbol = $1 ORDER BY time DESC LIMIT $2", symbol, limit)
            return [dict(row) for row in rows]

    async def get_tables(self) -> List[str]:
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
            """)
            return [row["table_name"] for row in rows]

    async def query_table(self, table: str, limit: int = 100, filters: Dict[str, str] = None) -> List[Dict[str, Any]]:
        if not self.pool:
            return []
            
        # Basic SQL injection prevention: ensure table name contains only alphanumeric and underscores
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', table):
            raise ValueError("Invalid table name")

        query = f"SELECT * FROM {table}"
        args = []
        
        if filters:
            where_clauses = []
            for i, (k, v) in enumerate(filters.items(), 1):
                if not re.match(r'^[a-zA-Z0-9_]+$', k):
                    continue
                where_clauses.append(f"{k}::text ILIKE ${i}")
                args.append(f"%{v}%")
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
                
        # Handle time-based sorting if time column exists, else just limit
        # This is a bit hacky but works for standard Timescale tables
        if table in ['trades', 'ticks']:
            query += f" ORDER BY time DESC"
        elif table == 'positions':
            query += f" ORDER BY created_at DESC"
            
        query += f" LIMIT ${len(args) + 1}"
        args.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
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
        strategy = trade_data.get("strategy_name") or order.get("strategy", "System")

        from datetime import datetime, timezone
        def parse_dt(dt_val):
            if isinstance(dt_val, (int, float)):
                # Unix timestamp from order executor
                return datetime.fromtimestamp(dt_val, tz=timezone.utc)
            if isinstance(dt_val, str):
                try:
                    return datetime.fromisoformat(dt_val.replace('Z', '+00:00'))
                except:
                    return datetime.now(timezone.utc)
            return dt_val if isinstance(dt_val, datetime) else datetime.now(timezone.utc)

        # open_time_ts is a Unix timestamp set at execution time in the order executor
        open_time_ts = trade_data.get("open_time_ts")
        open_time = parse_dt(open_time_ts) if open_time_ts else datetime.now(timezone.utc)
        close_time = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trades (time, id, symbol, side, entry_price, exit_price, quantity, pnl_usdt, pnl_pct, fee, open_time, close_time, strategy_name, stop_loss_price, take_profit_price, close_reason, ab_variant) VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)",
                str(trade_data.get("id", str(uuid.uuid4()))),
                symbol.upper(),
                side,
                float(trade_data.get("entry_price", 0)),
                float(trade_data.get("exit_price", 0)),
                float(qty),
                float(trade_data.get("pnl_usdt", 0)),
                float(trade_data.get("pnl_pct", 0)),
                float(trade_data.get("fee", 0)),
                open_time,
                close_time,
                strategy,
                float(trade_data.get("stop_loss_price", 0)),
                float(trade_data.get("take_profit_price", 0)),
                trade_data.get("close_reason", "UNKNOWN"),
                trade_data.get("ab_variant", "A")
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
