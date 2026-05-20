"""
Canonical database schema (idempotent). Single source of truth for Alembic + verify + legacy repair.
"""
from __future__ import annotations

HEAD_REVISION = "0001_baseline"

REQUIRED_TABLES = frozenset({
    "trades",
    "order_commands",
    "ohlcv",
    "daily_performance",
    "orders",
    "positions",
    "ticks",
})

REQUIRED_HYPERTABLES = frozenset({"trades", "ohlcv", "ticks"})

REQUIRED_TRADES_COLUMNS = frozenset({
    "time", "id", "symbol", "side", "entry_price", "exit_price", "quantity",
    "pnl_usdt", "pnl_pct", "open_time", "close_time", "strategy_name",
    "stop_loss_price", "take_profit_price", "close_reason", "fee", "ab_variant",
})

REQUIRED_TICKS_COLUMNS = frozenset({
    "time", "symbol", "price", "volume", "side",
    "bid_price", "ask_price", "bid_qty", "ask_qty", "timestamp_ms",
})


LEGACY_TRADES_MIGRATION = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'trades' AND column_name = 'trade_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'trades' AND column_name = 'entry_price'
    ) THEN
        ALTER TABLE IF EXISTS trades RENAME TO trades_legacy_pre_baseline;
    END IF;
END $$;
"""

BASELINE_STATEMENTS: tuple[str, ...] = (
    LEGACY_TRADES_MIGRATION,
    """
    CREATE TABLE IF NOT EXISTS trades (
        time TIMESTAMPTZ NOT NULL,
        id VARCHAR(50) NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        side VARCHAR(10) NOT NULL,
        entry_price DOUBLE PRECISION NOT NULL,
        exit_price DOUBLE PRECISION NOT NULL,
        quantity DOUBLE PRECISION NOT NULL,
        pnl_usdt DOUBLE PRECISION NOT NULL,
        pnl_pct DOUBLE PRECISION NOT NULL,
        open_time TIMESTAMPTZ NOT NULL,
        close_time TIMESTAMPTZ NOT NULL,
        strategy_name VARCHAR(50) NOT NULL,
        stop_loss_price DOUBLE PRECISION,
        take_profit_price DOUBLE PRECISION,
        close_reason VARCHAR(50) NOT NULL,
        fee DOUBLE PRECISION DEFAULT 0.0,
        ab_variant VARCHAR(1) NOT NULL DEFAULT 'A',
        PRIMARY KEY (time, id)
    );
    """,
    "SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades (symbol);",
    "CREATE INDEX IF NOT EXISTS ix_trades_strategy ON trades (strategy_name);",
    """
    CREATE TABLE IF NOT EXISTS order_commands (
        id VARCHAR(50) PRIMARY KEY,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        symbol VARCHAR(20) NOT NULL,
        type VARCHAR(10) NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        quantity DOUBLE PRECISION NOT NULL,
        strategy VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ohlcv (
        time TIMESTAMPTZ NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        volume DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (time, symbol)
    );
    """,
    "SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);",
    """
    CREATE TABLE IF NOT EXISTS daily_performance (
        date DATE PRIMARY KEY,
        total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        win_count INTEGER NOT NULL DEFAULT 0,
        loss_count INTEGER NOT NULL DEFAULT 0,
        win_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        max_drawdown DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        sharpe_ratio DOUBLE PRECISION NOT NULL DEFAULT 0.0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id UUID PRIMARY KEY,
        time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        symbol VARCHAR(20) NOT NULL,
        side VARCHAR(10) NOT NULL,
        price DECIMAL NOT NULL,
        quantity DECIMAL NOT NULL,
        status VARCHAR(20) NOT NULL,
        strategy VARCHAR(50),
        ab_variant CHAR(1),
        exchange_order_id VARCHAR(100)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        symbol VARCHAR(20) NOT NULL,
        ab_variant CHAR(1) NOT NULL,
        entry_time TIMESTAMPTZ NOT NULL,
        entry_price DECIMAL NOT NULL,
        quantity DECIMAL NOT NULL,
        side VARCHAR(10) NOT NULL,
        stop_loss DECIMAL,
        take_profit DECIMAL,
        PRIMARY KEY (symbol, ab_variant)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ticks (
        time TIMESTAMPTZ NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        volume DOUBLE PRECISION,
        side VARCHAR(10),
        bid_price DOUBLE PRECISION,
        ask_price DOUBLE PRECISION,
        bid_qty DOUBLE PRECISION,
        ask_qty DOUBLE PRECISION,
        timestamp_ms BIGINT
    );
    """,
    "SELECT create_hypertable('ticks', 'time', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS ix_ticks_symbol_time ON ticks (symbol, time DESC);",
    "ALTER TABLE ticks SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol');",
    "SELECT add_compression_policy('ticks', INTERVAL '2 hours', if_not_exists => TRUE);",
    "SELECT add_retention_policy('ticks', INTERVAL '24 hours', if_not_exists => TRUE);",
    # Legacy DBs: add columns if ticks existed with minimal schema
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS side VARCHAR(10);",
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS bid_price DOUBLE PRECISION;",
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS ask_price DOUBLE PRECISION;",
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS bid_qty DOUBLE PRECISION;",
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS ask_qty DOUBLE PRECISION;",
    "ALTER TABLE ticks ADD COLUMN IF NOT EXISTS timestamp_ms BIGINT;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fee DOUBLE PRECISION DEFAULT 0.0;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS ab_variant VARCHAR(1) NOT NULL DEFAULT 'A';",
)


async def apply_baseline_async(conn) -> None:
    for stmt in BASELINE_STATEMENTS:
        await conn.execute(stmt)


def apply_baseline_sync(connection) -> None:
    from sqlalchemy import text

    for stmt in BASELINE_STATEMENTS:
        connection.execute(text(stmt.strip()))
