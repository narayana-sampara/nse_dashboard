CREATE TABLE IF NOT EXISTS five_percent_strategy_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    market_date DATE NOT NULL,
    strategy_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    target_pct DOUBLE PRECISION NOT NULL,
    stop_loss_pct DOUBLE PRECISION NOT NULL,
    holding_days SMALLINT NOT NULL,
    probability_threshold DOUBLE PRECISION NOT NULL,
    initial_capital DOUBLE PRECISION NOT NULL,
    max_candidates INTEGER NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS five_percent_strategy_runs_market_date_idx
    ON five_percent_strategy_runs (market_date DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS five_percent_strategy_candidates (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES five_percent_strategy_runs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    close_price DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    stop_loss_price DOUBLE PRECISION NOT NULL,
    probability_score DOUBLE PRECISION NOT NULL,
    ai_score DOUBLE PRECISION NOT NULL,
    rank SMALLINT NOT NULL,
    expected_return_pct DOUBLE PRECISION NOT NULL,
    risk_reward_ratio DOUBLE PRECISION NOT NULL,
    avg_volume DOUBLE PRECISION,
    avg_turnover DOUBLE PRECISION,
    volatility DOUBLE PRECISION,
    rsi DOUBLE PRECISION,
    momentum_5d DOUBLE PRECISION,
    momentum_20d DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    trend_score DOUBLE PRECISION,
    relative_strength_score DOUBLE PRECISION,
    breakout_score DOUBLE PRECISION,
    risk_score DOUBLE PRECISION,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS five_percent_strategy_candidates_run_idx
    ON five_percent_strategy_candidates (run_id, rank);

CREATE INDEX IF NOT EXISTS five_percent_strategy_candidates_symbol_idx
    ON five_percent_strategy_candidates (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS five_percent_strategy_candidates_probability_idx
    ON five_percent_strategy_candidates (probability_score DESC);

CREATE INDEX IF NOT EXISTS five_percent_strategy_candidates_ai_score_idx
    ON five_percent_strategy_candidates (ai_score DESC);

CREATE TABLE IF NOT EXISTS five_percent_backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    backtest_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital DOUBLE PRECISION NOT NULL,
    final_capital DOUBLE PRECISION NOT NULL,
    total_return_pct DOUBLE PRECISION NOT NULL,
    target_pct DOUBLE PRECISION NOT NULL,
    stop_loss_pct DOUBLE PRECISION NOT NULL,
    holding_days SMALLINT NOT NULL,
    total_trades INTEGER NOT NULL,
    winning_trades INTEGER NOT NULL,
    losing_trades INTEGER NOT NULL,
    win_rate DOUBLE PRECISION NOT NULL,
    average_win_pct DOUBLE PRECISION,
    average_loss_pct DOUBLE PRECISION,
    max_drawdown_pct DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    longest_win_streak INTEGER,
    longest_loss_streak INTEGER,
    assumptions JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS five_percent_backtest_runs_created_idx
    ON five_percent_backtest_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS five_percent_backtest_trades (
    id BIGSERIAL PRIMARY KEY,
    backtest_id BIGINT NOT NULL REFERENCES five_percent_backtest_runs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    entry_date DATE NOT NULL,
    exit_date DATE,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    target_price DOUBLE PRECISION NOT NULL,
    stop_loss_price DOUBLE PRECISION NOT NULL,
    result TEXT NOT NULL,
    return_pct DOUBLE PRECISION NOT NULL,
    capital_before DOUBLE PRECISION NOT NULL,
    capital_after DOUBLE PRECISION NOT NULL,
    holding_days SMALLINT NOT NULL,
    exit_reason TEXT NOT NULL,
    probability_score DOUBLE PRECISION,
    ai_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS five_percent_backtest_trades_backtest_idx
    ON five_percent_backtest_trades (backtest_id);

CREATE INDEX IF NOT EXISTS five_percent_backtest_trades_symbol_idx
    ON five_percent_backtest_trades (symbol, entry_date DESC);

CREATE TABLE IF NOT EXISTS five_percent_paper_trades (
    id BIGSERIAL PRIMARY KEY,
    signal_id TEXT,
    symbol TEXT NOT NULL,
    entry_date DATE NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    target_price DOUBLE PRECISION NOT NULL,
    stop_loss_price DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION,
    status TEXT NOT NULL,
    exit_date DATE,
    exit_price DOUBLE PRECISION,
    return_pct DOUBLE PRECISION,
    capital_before DOUBLE PRECISION NOT NULL,
    capital_after DOUBLE PRECISION,
    exit_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS five_percent_paper_trades_symbol_idx
    ON five_percent_paper_trades (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS five_percent_paper_trades_status_idx
    ON five_percent_paper_trades (status, created_at DESC);
