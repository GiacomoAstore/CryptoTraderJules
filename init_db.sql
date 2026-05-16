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

-- Table for tracking orders (Execution Gateway)
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DECIMAL NOT NULL,
    quantity DECIMAL NOT NULL,
    status VARCHAR(20) NOT NULL, -- PENDING, FILLED, FAILED, CANCELLED
    strategy VARCHAR(50),
    ab_variant CHAR(1),
    exchange_order_id VARCHAR(100)
);

-- Table for tracking open positions
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

-- Table for daily performance persistence
CREATE TABLE IF NOT EXISTS daily_performance (
    date DATE PRIMARY KEY,
    total_pnl DECIMAL NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    win_rate DECIMAL NOT NULL DEFAULT 0,
    max_drawdown DECIMAL NOT NULL DEFAULT 0,
    sharpe_ratio DECIMAL NOT NULL DEFAULT 0
);

