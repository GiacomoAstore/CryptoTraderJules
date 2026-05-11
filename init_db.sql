-- Create ticks table for raw orderbook/price updates
CREATE TABLE IF NOT EXISTS ticks (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION
);

-- Convert to TimescaleDB hypertable
SELECT create_hypertable('ticks', 'time', if_not_exists => TRUE);

-- Create index on symbol for faster lookups
CREATE INDEX IF NOT EXISTS ix_symbol_time ON ticks (symbol, time DESC);

-- Create trades table for actual executed orders
CREATE TABLE IF NOT EXISTS trades (
    time TIMESTAMPTZ NOT NULL,
    trade_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    strategy VARCHAR(50)
);

-- Convert to TimescaleDB hypertable
SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);

-- Create index on symbol for trades
CREATE INDEX IF NOT EXISTS ix_trades_symbol_time ON trades (symbol, time DESC);

-- Create daily performance table
CREATE TABLE IF NOT EXISTS daily_performance (
    date DATE PRIMARY KEY,
    total_pnl DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    total_trades INTEGER
);
