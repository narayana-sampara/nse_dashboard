CREATE TABLE IF NOT EXISTS worker_task_keys (
    key TEXT PRIMARY KEY,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS signal_alerts (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    run_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sector TEXT,
    signal TEXT NOT NULL,
    score INTEGER NOT NULL,
    price DOUBLE PRECISION,
    payload JSONB NOT NULL,
    UNIQUE (run_key, symbol, signal)
);

CREATE INDEX IF NOT EXISTS signal_alerts_created_at_idx
    ON signal_alerts (created_at DESC);
