ALTER TABLE monthly_prediction_runs
    ADD COLUMN IF NOT EXISTS regime JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE monthly_prediction_runs
    ADD COLUMN IF NOT EXISTS strategy_name TEXT NOT NULL DEFAULT 'conservative_nse_monthly';
ALTER TABLE monthly_prediction_runs
    ADD COLUMN IF NOT EXISTS strategy_version TEXT NOT NULL DEFAULT '2.0.0';
ALTER TABLE monthly_prediction_runs
    ADD COLUMN IF NOT EXISTS selection_method JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE weekly_prediction_runs
    ADD COLUMN IF NOT EXISTS monthly_regime TEXT NOT NULL DEFAULT 'UNAVAILABLE';

CREATE TABLE IF NOT EXISTS paper_portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_version TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS paper_portfolio_snapshots_latest_idx
    ON paper_portfolio_snapshots (captured_at DESC);
