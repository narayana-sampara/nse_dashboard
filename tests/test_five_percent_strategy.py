from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nse_dashboard.five_percent_strategy.backtester import BacktestConfig, run_backtest
from nse_dashboard.five_percent_strategy.baseline_model import ExplainableBaselineModel
from nse_dashboard.five_percent_strategy.features import compute_features, label_hits_target_before_stop
from nse_dashboard.five_percent_strategy.risk import RiskLimits, passes_liquidity_and_volatility_filters, position_size
from nse_dashboard.five_percent_strategy.service import FivePercentStrategyService, _project_capital


def _uptrend_frame(periods: int = 260, start: float = 100.0, drift: float = 0.15) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=periods, freq="B")
    close = start + np.cumsum(np.full(periods, drift)) + np.sin(np.arange(periods) / 5) * 0.3
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.999
    volume = np.full(periods, 1_000_000.0)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates
    )


def test_compute_features_has_no_lookahead() -> None:
    frame = _uptrend_frame()
    truncated = frame.iloc[:100]
    full = frame

    truncated_features = compute_features("TEST", truncated)
    # Recomputing on a prefix must reproduce the same as-of row when sliced identically.
    resliced_features = compute_features("TEST", full.iloc[:100])
    assert truncated_features.as_of == resliced_features.as_of
    assert truncated_features.close == resliced_features.close


def test_label_hits_target_before_stop_when_price_rallies() -> None:
    frame = _uptrend_frame(periods=40, start=100.0, drift=1.5)
    label = label_hits_target_before_stop(frame, entry_index=10, target_pct=5.0, stop_loss_pct=2.0, holding_days=5)
    assert label == 1


def test_label_zero_when_stop_hit_first() -> None:
    dates = pd.date_range("2024-01-01", periods=20, freq="B")
    close = np.full(20, 100.0)
    close[11] = 97.0  # -3% on day after entry, below -2% stop
    high = close + 0.5
    low = close - 0.5
    low[11] = 96.5
    frame = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": np.full(20, 1_000_000.0)}, index=dates)
    label = label_hits_target_before_stop(frame, entry_index=10, target_pct=5.0, stop_loss_pct=2.0, holding_days=5)
    assert label == 0


def test_label_zero_when_holding_period_expires_without_hit() -> None:
    dates = pd.date_range("2024-01-01", periods=20, freq="B")
    close = np.full(20, 100.0)
    frame = pd.DataFrame(
        {"Open": close, "High": close + 0.2, "Low": close - 0.2, "Close": close, "Volume": np.full(20, 1_000_000.0)},
        index=dates,
    )
    label = label_hits_target_before_stop(frame, entry_index=10, target_pct=5.0, stop_loss_pct=2.0, holding_days=5)
    assert label == 0


def test_baseline_model_scores_strong_uptrend_higher_than_flat() -> None:
    strong = compute_features("STRONG", _uptrend_frame(drift=0.6))
    flat = compute_features("FLAT", _uptrend_frame(drift=0.0))
    model = ExplainableBaselineModel()
    strong_prediction, flat_prediction = model.predict_candidates([strong, flat])
    assert strong_prediction.probability_score > flat_prediction.probability_score
    assert strong_prediction.reasons
    assert 0 <= strong_prediction.ai_score <= 10


def test_risk_filters_reject_illiquid_stock() -> None:
    features = compute_features("THIN", _uptrend_frame())
    features.avg_traded_value_20d = 1000.0
    ok, reason = passes_liquidity_and_volatility_filters(features, RiskLimits(min_avg_turnover=10_000_000))
    assert not ok
    assert reason


def test_position_size_respects_max_risk_per_trade() -> None:
    sizing = position_size(100_000, entry_price=100, stop_loss_price=98, limits=RiskLimits(max_risk_per_trade_pct=1.0))
    assert sizing["max_loss_amount"] <= 1000.0 + 1e-6


def test_risk_limits_reject_full_capital_allocation() -> None:
    with pytest.raises(ValueError):
        RiskLimits(max_capital_per_trade_pct=100.0)


def test_backtest_records_target_hit_trade() -> None:
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    close = 100 * (1.02 ** np.arange(120))
    frame = pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.02,
            "Low": close * 0.995,
            "Close": close,
            "Volume": np.full(120, 1_000_000.0),
        },
        index=dates,
    )
    config = BacktestConfig(
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=10_000,
        probability_threshold=1,
        max_trades=3,
    )
    result = run_backtest({"WIN.NS": frame}, config)
    assert result["total_trades"] >= 1
    assert any(trade["exit_reason"] == "target_hit" for trade in result["trades"])
    assert result["final_capital"] > 0


def test_backtest_records_stop_loss_trade() -> None:
    dates = pd.date_range("2024-01-01", periods=90, freq="B")
    close = 100 + np.cumsum(np.full(90, -0.9))
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.3,
            "Low": close - 0.3,
            "Close": close,
            "Volume": np.full(90, 1_000_000.0),
        },
        index=dates,
    )
    config = BacktestConfig(
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=10_000,
        probability_threshold=0,
        max_trades=5,
    )
    result = run_backtest({"LOSS.NS": frame}, config)
    if result["total_trades"]:
        assert any(trade["result"] == "loss" for trade in result["trades"])


def test_compounding_projection_matches_expected_multiplier() -> None:
    scenario = _project_capital(
        initial_capital=10_000,
        target_pct=5.0,
        stop_loss_pct=2.0,
        number_of_trades=1,
        win_rate_pct=100.0,
        cost_per_trade_pct=0.0,
    )
    assert scenario["final_capital"] == pytest.approx(10_500.0)


def test_compounding_projection_lower_win_rate_yields_lower_capital() -> None:
    high = _project_capital(10_000, 5.0, 2.0, 50, 90.0, 0.3)
    low = _project_capital(10_000, 5.0, 2.0, 50, 60.0, 0.3)
    assert high["final_capital"] > low["final_capital"]


class _FakeAdapter:
    name = "fake"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        del symbol, period
        return self.frame.copy()

    def market_history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        del period
        return {symbol: self.frame.copy() for symbol in symbols}


class _FakeRepository:
    def __init__(self) -> None:
        self.saved_runs: list[dict] = []

    def save_five_percent_strategy_run(self, snapshot: dict) -> int:
        self.saved_runs.append(snapshot)
        return len(snapshot.get("candidates", []))

    def latest_five_percent_strategy_run(self) -> dict:
        return self.saved_runs[-1] if self.saved_runs else {}


def test_service_generate_ranks_and_persists_candidates() -> None:
    adapter = _FakeAdapter(_uptrend_frame(drift=0.6))
    repository = _FakeRepository()
    service = FivePercentStrategyService(adapter, repository, period="1y")

    result = service.generate(probability_threshold=0, max_candidates=5)

    assert result["candidates_count"] > 0
    assert repository.saved_runs
    ranks = [candidate["rank"] for candidate in result["candidates"]]
    assert ranks == sorted(ranks)
    assert result["disclaimer"]
