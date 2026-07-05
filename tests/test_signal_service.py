from __future__ import annotations

import numpy as np
import pandas as pd

from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.signals import SignalService
from sector_map import SECTOR_MAP


class FakeAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.market_calls = 0
        dates = pd.date_range("2025-01-01", periods=220, freq="B")
        self.frame = pd.DataFrame(
            {
                "Close": np.linspace(100, 160, len(dates)),
                "Volume": np.full(len(dates), 1_000_000),
            },
            index=dates,
        )

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        return self.frame.copy()

    def market_history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        self.market_calls += 1
        return {symbol: self.frame.copy() for symbol in symbols}


class FakeSnapshots:
    def __init__(self) -> None:
        self.signals = []
        self.scans = []

    def save_signal(self, snapshot) -> None:
        self.signals.append(snapshot)

    def save_market_scan(self, snapshot, idempotency_key=None) -> None:
        self.scans.append(snapshot)

    def save_alerts(self, snapshot, idempotency_key: str) -> int:
        return 0

    def recent_alerts(self, limit: int = 100):
        return []

    def signal_history(self, symbol: str, limit: int = 100):
        return self.signals[-limit:]

    def save_weekly_predictions(self, snapshot) -> int:
        self.weekly = snapshot
        return snapshot["predictions_count"]

    def latest_weekly_predictions(self, max_price: float = 100, limit_per_sector: int = 5):
        del max_price, limit_per_sector
        return getattr(
            self,
            "weekly",
            {"generated_at": None, "predictions_count": 0, "sectors": []},
        )

    def weekly_prediction_history(self, symbol: str, limit: int = 100):
        del symbol, limit
        return []

    def save_monthly_predictions(self, snapshot) -> int:
        if not hasattr(self, "monthly"):
            self.monthly = {}
        self.monthly[snapshot["horizon_months"]] = snapshot
        return snapshot["predictions_count"]

    def latest_monthly_predictions(self, horizon_months: int, max_price: float = 100, limit_per_sector: int = 5):
        del max_price, limit_per_sector
        return getattr(self, "monthly", {}).get(
            horizon_months,
            {"generated_at": None, "horizon_months": horizon_months, "predictions_count": 0, "score_method": {}, "sectors": []},
        )

    def monthly_prediction_history(self, symbol: str, horizon_months=None, limit: int = 100):
        del symbol, horizon_months, limit
        return []

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


def test_evaluate_uses_injected_adapter() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache())
    result = service.evaluate("reliance.ns")
    assert result["symbol"] == "RELIANCE.NS"
    assert result["source"] == "fake"


def test_dashboard_scan_is_cached() -> None:
    adapter = FakeAdapter()
    service = SignalService(adapter, MemoryTtlCache(), cache_seconds=30)

    first = service.scan_market()
    second = service.scan_market()

    assert adapter.market_calls == 1
    assert first == second
    assert first["universe_size"] == len(SECTOR_MAP)


def test_evaluation_and_fresh_scan_are_snapshotted() -> None:
    snapshots = FakeSnapshots()
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=snapshots)

    result = service.evaluate("RELIANCE.NS")
    service.scan_market()
    service.scan_market()

    assert snapshots.signals == [result]
    assert result["strategy"] == "composite_technical"
    assert len(snapshots.scans) == 1
