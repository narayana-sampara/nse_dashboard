CREATE TABLE IF NOT EXISTS market_quote_snapshots (
    captured_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    price DOUBLE PRECISION,
    market_time TIMESTAMPTZ,
    payload JSONB NOT NULL
);

SELECT create_hypertable(
    'market_quote_snapshots',
    by_range('captured_at'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS market_quote_snapshots_symbol_time_idx
    ON market_quote_snapshots (symbol, captured_at DESC);
