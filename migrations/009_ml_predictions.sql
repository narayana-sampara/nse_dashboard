CREATE TABLE IF NOT EXISTS ml_prediction_runs (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    universe_size INTEGER NOT NULL DEFAULT 0,
    predictions_count INTEGER NOT NULL DEFAULT 0,
    failures JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ml_prediction_runs_generated_at
    ON ml_prediction_runs (generated_at DESC);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES ml_prediction_runs(id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    rank INTEGER NOT NULL,
    current_price NUMERIC,
    target_price_1y NUMERIC,
    implied_cagr_pct NUMERIC,
    probability_positive NUMERIC,
    conviction TEXT,
    shap_values JSONB NOT NULL DEFAULT '{}'::jsonb,
    dynamic_thesis TEXT,
    payload JSONB NOT NULL,
    UNIQUE (run_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_run_rank
    ON ml_predictions (run_id, rank);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_symbol_generated
    ON ml_predictions (symbol, generated_at DESC);
