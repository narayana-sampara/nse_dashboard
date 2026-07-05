from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.monthly_predictions import (
    ExplainableMonthlyModel,
    MonthlyPredictionService,
)
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeSnapshots


class MonthlyAdapter:
    name = "monthly-fake"

    def __init__(self, price: float = 80) -> None:
        dates = pd.date_range("2024-01-01", periods=520, freq="B")
        close = np.linspace(price * 0.55, price, len(dates))
        self.frame = pd.DataFrame(
            {"Close": close, "Volume": np.full(len(dates), 2_000_000)},
            index=dates,
        )

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        del symbol, period
        return self.frame.copy()

    def market_history(self, symbols: list[str], period: str):
        del period
        return {symbol: self.frame.copy() for symbol in symbols}


def test_monthly_score_is_explainable_and_totals_100_maximum() -> None:
    adapter = MonthlyAdapter()
    result = ExplainableMonthlyModel().predict("TEST.NS", "Test", adapter.frame, 6)

    assert result["horizon_months"] == 6
    assert sum(result["score_maximums"].values()) == 100
    assert abs(result["score"] - sum(result["score_breakdown"].values())) < 0.05
    assert 0 <= result["score"] <= 100


def test_monthly_generation_is_persisted_by_horizon() -> None:
    snapshots = FakeSnapshots()
    result = MonthlyPredictionService(MonthlyAdapter(), snapshots).generate(
        3, min_score=0, min_average_traded_value=0
    )

    assert result["horizon_months"] == 3
    assert result["predictions_count"] > 0
    assert result["score_method"] == {
        "trend": 30, "momentum": 30, "volume": 10,
        "rsi_quality": 10, "risk_control": 20,
    }
    assert snapshots.monthly[3] == result


def test_monthly_generation_has_no_default_price_cap() -> None:
    result = MonthlyPredictionService(MonthlyAdapter(price=500), FakeSnapshots()).generate(
        3, min_score=0, min_average_traded_value=0, limit_per_sector=1
    )

    assert result["predictions_count"] > 0
    assert all(
        pick["price"] > 100
        for sector in result["sectors"]
        for pick in sector["picks"]
    )


def test_monthly_api_selects_interval_and_reads_persisted_result() -> None:
    snapshots = FakeSnapshots()
    service = SignalService(MonthlyAdapter(), MemoryTtlCache(), snapshots=snapshots)
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        generated = client.post(
            "/api/v1/monthly-predictions/generate",
            params={"horizon_months": 6, "min_score": 0, "min_average_traded_value": 0},
        )
        latest = client.get(
            "/api/v1/monthly-predictions", params={"horizon_months": 6}
        )

    assert generated.status_code == 200
    assert generated.json()["horizon_months"] == 6
    assert latest.status_code == 200
    assert latest.json()["model"]["name"] == "explainable_monthly_ranker"
