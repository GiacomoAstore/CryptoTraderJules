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
