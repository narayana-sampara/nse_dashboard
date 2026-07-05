from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.signals import SignalService
from nse_dashboard.services.weekly_predictions import (
    ExplainableWeeklyModel,
    WeeklyPredictionService,
)
from sector_map import SECTOR_MAP
from tests.test_signal_service import FakeSnapshots


class WeeklyAdapter:
    name = "weekly-fake"

    def __init__(self, price: float = 80) -> None:
        dates = pd.date_range("2025-01-01", periods=240, freq="B")
        close = np.linspace(price * 0.65, price, len(dates))
        close[-5:] = np.linspace(price * 0.94, price, 5)
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


def test_model_returns_versioned_explainable_prediction() -> None:
    adapter = WeeklyAdapter()
    prediction = ExplainableWeeklyModel().predict(
        "TEST.NS", "Test", adapter.frame
    )

    assert prediction["price"] == 80
    assert 0 <= prediction["target_probability"] <= 1
    assert prediction["predicted_5d_return_pct"] > 0
    assert prediction["reasons"]
    assert set(prediction["features"]) >= {"momentum_5d", "rsi_14", "volume_ratio"}


def test_generation_filters_price_ranks_each_sector_and_persists() -> None:
    snapshots = FakeSnapshots()
    service = WeeklyPredictionService(WeeklyAdapter(), snapshots)

    result = service.generate(
        min_probability=0,
        min_expected_return=-100,
        min_average_traded_value=0,
        limit_per_sector=5,
    )

    assert result["model"]["version"] == "1.0.0"
    expected = sum(
        min(5, sum(1 for value in SECTOR_MAP.values() if value == sector))
        for sector in set(SECTOR_MAP.values())
    )
    assert result["predictions_count"] == expected
    assert all(
        1 <= pick["sector_rank"] <= 5 and pick["price"] <= 100
        for sector in result["sectors"]
        for pick in sector["picks"]
    )
    assert snapshots.weekly == result


def test_generation_has_no_default_price_cap() -> None:
    result = WeeklyPredictionService(WeeklyAdapter(price=500), FakeSnapshots()).generate(
        min_probability=0,
        min_expected_return=-100,
        min_average_traded_value=0,
        limit_per_sector=1,
    )

    assert result["predictions_count"] > 0
    assert all(
        pick["price"] == 500
        for sector in result["sectors"]
        for pick in sector["picks"]
    )


def test_weekly_prediction_api_generates_and_reads_persisted_result() -> None:
    snapshots = FakeSnapshots()
    signal_service = SignalService(
        WeeklyAdapter(), MemoryTtlCache(), snapshots=snapshots
    )
    app = create_app(Settings(environment="test"), signal_service)
    with TestClient(app) as client:
        generated = client.post(
            "/api/v1/weekly-predictions/generate",
            params={
                "min_probability": 0,
                "min_expected_return": -100,
                "min_average_traded_value": 0,
            },
        )
        latest = client.get("/api/v1/weekly-predictions")

    assert generated.status_code == 200
    assert generated.json()["predictions_count"] > 0
    assert latest.status_code == 200
    assert latest.json()["model"]["name"] == "explainable_weekly_ranker"
