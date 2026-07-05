from __future__ import annotations

import time

import anyio
import pytest
from fastapi.testclient import TestClient

from nse_dashboard.api.app import _run_generation_with_timeout, create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.signals import SignalService
from nse_dashboard.streaming.broker import MemoryEventBroker
from nse_dashboard.workers.tasks import (
    generate_monthly_predictions,
    run_market_pipeline,
)
from tests.test_signal_service import FakeAdapter, FakeSnapshots


def test_prediction_generation_timeout_abandons_slow_operation() -> None:
    def slow_operation() -> dict:
        time.sleep(1)
        return {"status": "late"}

    with pytest.raises(TimeoutError):
        anyio.run(_run_generation_with_timeout, slow_operation, 0.01)


def test_dashboard_refresh_is_queued_when_background_workers_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued_calls = []

    def fake_apply_async(**kwargs):
        queued_calls.append(kwargs)

        class Result:
            id = "queued-dashboard"

        return Result()

    monkeypatch.setattr(run_market_pipeline, "apply_async", fake_apply_async)
    adapter = FakeAdapter()
    service = SignalService(adapter, MemoryTtlCache(), snapshots=FakeSnapshots())
    settings = Settings(environment="development", redis_url="redis://redis:6379/0")

    with TestClient(create_app(settings, service, MemoryEventBroker())) as client:
        response = client.get("/api/v1/dashboard?refresh=true")

    assert response.status_code == 202
    assert response.json()["generation_status"]["state"] == "queued"
    assert queued_calls == [{"queue": "ingestion"}]
    assert adapter.market_calls == 0


def test_monthly_generation_is_queued_when_background_workers_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued_calls = []

    def fake_apply_async(**kwargs):
        queued_calls.append(kwargs)

        class Result:
            id = "queued-monthly"

        return Result()

    monkeypatch.setattr(generate_monthly_predictions, "apply_async", fake_apply_async)
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    settings = Settings(environment="development", redis_url="redis://redis:6379/0")

    with TestClient(create_app(settings, service, MemoryEventBroker())) as client:
        response = client.post(
            "/api/v1/monthly-predictions/generate",
            params={"horizon_months": 3, "limit_per_sector": 4},
        )

    assert response.status_code == 202
    assert response.json()["generation_status"]["state"] == "queued"
    assert queued_calls[0]["queue"] == "computation"
    assert queued_calls[0]["kwargs"]["horizon_months"] == 3
    assert queued_calls[0]["kwargs"]["limit_per_sector"] == 4
