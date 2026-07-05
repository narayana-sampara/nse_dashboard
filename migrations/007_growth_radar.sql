CREATE TABLE IF NOT EXISTS filing_documents (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload_hash TEXT NOT NULL,
    extraction_status TEXT NOT NULL DEFAULT 'COMPLETE'
        CHECK (extraction_status IN ('COMPLETE', 'PARTIAL', 'MANUAL_REVIEW', 'FAILED')),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (symbol, source, source_url, payload_hash)
);

CREATE INDEX IF NOT EXISTS filing_documents_point_in_time_idx
    ON filing_documents (symbol, known_at DESC, published_at DESC);

CREATE TABLE IF NOT EXISTS growth_factor_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    as_of DATE NOT NULL,
    known_at TIMESTAMPTZ NOT NULL,
    source_version TEXT NOT NULL,
    freshness_status TEXT NOT NULL DEFAULT 'CURRENT',
    features JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, as_of, known_at, source_version)
);

CREATE INDEX IF NOT EXISTS growth_factor_snapshots_latest_idx
    ON growth_factor_snapshots (symbol, known_at DESC, as_of DESC);

CREATE TABLE IF NOT EXISTS growth_radar_runs (
    id BIGSERIAL PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    market_date DATE,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS growth_radar_runs_latest_idx
    ON growth_radar_runs (market_date DESC, generated_at DESC);

CREATE TABLE IF NOT EXISTS growth_first_signals (
    symbol TEXT PRIMARY KEY,
    signal_date DATE NOT NULL,
    signal_price DOUBLE PRECISION NOT NULL CHECK (signal_price > 0),
    initial_state TEXT NOT NULL,
    initial_score DOUBLE PRECISION NOT NULL,
    radar_run_id BIGINT REFERENCES growth_radar_runs(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS growth_projection_runs (
    id BIGSERIAL PRIMARY KEY,
    radar_run_id BIGINT NOT NULL REFERENCES growth_radar_runs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    current_price DOUBLE PRECISION NOT NULL,
    confidence_pct DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS growth_projection_runs_symbol_idx
    ON growth_projection_runs (symbol, generated_at DESC);

CREATE TABLE IF NOT EXISTS growth_signal_outcomes (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    signal_date DATE NOT NULL,
    evaluation_date DATE NOT NULL,
    stock_return_12m_pct DOUBLE PRECISION,
    benchmark_return_12m_pct DOUBLE PRECISION,
    stock_return_24m_pct DOUBLE PRECISION,
    benchmark_return_24m_pct DOUBLE PRECISION,
    maximum_drawdown_pct DOUBLE PRECISION,
    lead_time_days INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (symbol, signal_date, evaluation_date)
);
