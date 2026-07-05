CREATE TABLE IF NOT EXISTS monthly_prediction_runs (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    market_date DATE NOT NULL,
    horizon_months SMALLINT NOT NULL CHECK (horizon_months BETWEEN 1 AND 12),
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    filters JSONB NOT NULL,
    score_method JSONB NOT NULL,
    universe_size INTEGER NOT NULL,
    eligible_stocks INTEGER NOT NULL,
    failures JSONB NOT NULL,
    UNIQUE (market_date, horizon_months, model_version)
);

CREATE TABLE IF NOT EXISTS monthly_predictions (
    run_id BIGINT NOT NULL REFERENCES monthly_prediction_runs(id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ NOT NULL,
    market_date DATE NOT NULL,
    horizon_months SMALLINT NOT NULL,
    symbol TEXT NOT NULL,
    sector TEXT NOT NULL,
    sector_rank SMALLINT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    predicted_return_pct DOUBLE PRECISION NOT NULL,
    target_probability DOUBLE PRECISION NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    risk_score DOUBLE PRECISION NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (market_date, horizon_months, model_version, symbol)
);

CREATE INDEX IF NOT EXISTS monthly_prediction_runs_latest_idx
    ON monthly_prediction_runs (horizon_months, market_date DESC, generated_at DESC);

CREATE INDEX IF NOT EXISTS monthly_predictions_rank_idx
    ON monthly_predictions (run_id, sector, sector_rank);

CREATE INDEX IF NOT EXISTS monthly_predictions_symbol_idx
    ON monthly_predictions (symbol, horizon_months, market_date DESC);
