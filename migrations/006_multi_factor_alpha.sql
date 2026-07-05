CREATE TABLE IF NOT EXISTS fundamental_feature_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    fiscal_period_end DATE NOT NULL,
    period_type TEXT NOT NULL CHECK (period_type IN ('QUARTERLY', 'ANNUAL', 'TTM')),
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_hash TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 100),
    grade CHAR(1) NOT NULL CHECK (grade IN ('A', 'B', 'C', 'D', 'F')),
    coverage TEXT NOT NULL,
    features JSONB NOT NULL,
    contributions JSONB NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (symbol, fiscal_period_end, period_type, source, source_version, payload_hash)
);

CREATE INDEX IF NOT EXISTS fundamental_features_point_in_time_idx
    ON fundamental_feature_snapshots (symbol, known_at DESC, fiscal_period_end DESC);

CREATE TABLE IF NOT EXISTS sentiment_feature_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 100),
    composite_score DOUBLE PRECISION NOT NULL CHECK (composite_score BETWEEN -1 AND 1),
    trend TEXT NOT NULL CHECK (trend IN ('Bullish', 'Neutral', 'Bearish')),
    coverage TEXT NOT NULL,
    features JSONB NOT NULL,
    contributions JSONB NOT NULL,
    UNIQUE (symbol, as_of, model_version)
);

CREATE INDEX IF NOT EXISTS sentiment_features_latest_idx
    ON sentiment_feature_snapshots (symbol, as_of DESC);

CREATE TABLE IF NOT EXISTS legal_risk_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    source_version TEXT NOT NULL,
    risk_quotient DOUBLE PRECISION NOT NULL CHECK (risk_quotient BETWEEN 0 AND 100),
    risk_flag TEXT NOT NULL CHECK (risk_flag IN ('High', 'Medium', 'Low', 'Unknown')),
    coverage TEXT NOT NULL,
    features JSONB NOT NULL,
    contributions JSONB NOT NULL,
    UNIQUE (symbol, as_of, source_version)
);

CREATE INDEX IF NOT EXISTS legal_risk_latest_idx
    ON legal_risk_snapshots (symbol, as_of DESC);

CREATE TABLE IF NOT EXISTS options_feature_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 100),
    coverage TEXT NOT NULL,
    features JSONB NOT NULL,
    contributions JSONB NOT NULL,
    UNIQUE (symbol, as_of, model_version)
);

CREATE INDEX IF NOT EXISTS options_features_latest_idx
    ON options_feature_snapshots (symbol, as_of DESC);

CREATE TABLE IF NOT EXISTS alpha_ranking_runs (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    market_date DATE,
    horizon TEXT NOT NULL CHECK (horizon IN ('weekly', 'monthly')),
    horizon_months SMALLINT,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    weights JSONB NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS alpha_ranking_runs_latest_idx
    ON alpha_ranking_runs (horizon, horizon_months, market_date DESC, generated_at DESC);
