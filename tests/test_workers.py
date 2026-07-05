from datetime import datetime, timezone

import pandas as pd
import pytest

from nse_dashboard.infrastructure.idempotency import (
    MemoryIdempotencyStore,
    TaskAlreadyRunning,
    execute_once,
)
from nse_dashboard.workers.celery_app import app
from nse_dashboard.workers.serialization import deserialize_histories, serialize_histories
from nse_dashboard.workers.tasks import scheduled_run_key


def test_execute_once_reuses_completed_result() -> None:
    store = MemoryIdempotencyStore()
    calls = []

    first = execute_once(store, "stage:run", 60, 60, lambda: calls.append(1) or {"ok": True})
    second = execute_once(store, "stage:run", 60, 60, lambda: calls.append(2) or {"ok": False})

    assert first == second == {"ok": True}
    assert calls == [1]


def test_execute_once_releases_lock_after_failure() -> None:
    store = MemoryIdempotencyStore()

    with pytest.raises(ValueError):
        execute_once(store, "stage:run", 60, 60, lambda: (_ for _ in ()).throw(ValueError()))

    assert execute_once(store, "stage:run", 60, 60, lambda: "retried") == "retried"


def test_running_operation_is_not_started_twice() -> None:
    store = MemoryIdempotencyStore()
    assert store.acquire("stage:run", 60) is not None
    with pytest.raises(TaskAlreadyRunning):
        execute_once(store, "stage:run", 60, 60, lambda: None)


def test_market_history_serialization_round_trip() -> None:
    index = pd.date_range("2026-01-01", periods=2, freq="D")
    source = {"ABC.NS": pd.DataFrame({"Close": [1.5, 2.5], "Volume": [10, 20]}, index=index)}

    restored = deserialize_histories(serialize_histories(source))["ABC.NS"]

    assert restored["Close"].tolist() == [1.5, 2.5]
    assert restored["Volume"].tolist() == [10.0, 20.0]
    assert restored.index.tolist() == index.tolist()


def test_worker_routes_and_schedule_are_explicit() -> None:
    routes = app.conf.task_routes
    assert routes["workers.ingest_market_data"]["queue"] == "ingestion"
    assert routes["workers.compute_signals"]["queue"] == "computation"
    assert routes["workers.persist_snapshot"]["queue"] == "snapshots"
    assert routes["workers.evaluate_alerts"]["queue"] == "alerts"
    assert routes["workers.generate_weekly_predictions"]["queue"] == "computation"
    assert routes["workers.generate_monthly_predictions"]["queue"] == "computation"
    assert routes["workers.generate_alpha_rankings"]["queue"] == "computation"
    assert routes["workers.generate_growth_radar"]["queue"] == "computation"
    assert routes["workers.ingest_growth_features"]["queue"] == "ingestion"
    assert routes["workers.ingest_filing_document"]["queue"] == "ingestion"
    assert routes["workers.ingest_fundamental_features"]["queue"] == "ingestion"
    assert routes["workers.aggregate_sentiment_features"]["queue"] == "computation"
    assert routes["workers.ingest_legal_risk"]["queue"] == "ingestion"
    assert "scheduled-market-pipeline" in app.conf.beat_schedule
    assert "daily-weekly-predictions" in app.conf.beat_schedule
    assert "scheduled-monthly-predictions" in app.conf.beat_schedule
    assert "weekly-alpha-rankings" in app.conf.beat_schedule
    assert "monthly-alpha-rankings" in app.conf.beat_schedule
    assert "weekly-growth-radar" in app.conf.beat_schedule


def test_scheduled_run_key_is_stable_within_interval() -> None:
    first = datetime(2026, 1, 1, 10, 0, 1, tzinfo=timezone.utc)
    second = datetime(2026, 1, 1, 10, 4, 59, tzinfo=timezone.utc)
    assert scheduled_run_key(first, 300) == scheduled_run_key(second, 300)
