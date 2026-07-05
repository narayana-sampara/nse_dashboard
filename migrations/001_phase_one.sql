CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS signal_snapshots (
    captured_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signal TEXT NOT NULL,
    price DOUBLE PRECISION,
    source TEXT,
    market_time TIMESTAMPTZ,
    payload JSONB NOT NULL
);

SELECT create_hypertable(
    'signal_snapshots',
    by_range('captured_at'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS signal_snapshots_symbol_time_idx
    ON signal_snapshots (symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS market_scan_snapshots (
    captured_at TIMESTAMPTZ NOT NULL,
    market_date DATE,
    strategy TEXT NOT NULL,
    source TEXT,
    payload JSONB NOT NULL
);

SELECT create_hypertable(
    'market_scan_snapshots',
    by_range('captured_at'),
    if_not_exists => TRUE
);
