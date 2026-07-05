from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from nse_dashboard.infrastructure.idempotency import TaskAlreadyRunning, execute_once
from nse_dashboard.services.ml_forecast_service import MLForecastService
from nse_dashboard.workers.celery_app import app
from nse_dashboard.workers.runtime import get_runtime


def _execute_task(task, stage: str, run_key: str, operation: Callable[[], Any]) -> Any:
    runtime = get_runtime()
    try:
        return execute_once(
            runtime.idempotency,
            f"{stage}:{run_key}",
            runtime.settings.idempotency_lock_ttl_seconds,
            runtime.settings.idempotency_ttl_seconds,
            operation,
        )
    except TaskAlreadyRunning as exc:
        raise task.retry(exc=exc, countdown=5, max_retries=12) from exc
    except Exception as exc:
        raise task.retry(exc=exc, countdown=60, max_retries=2) from exc


@app.task(bind=True, name="tasks.run_ml_inference")
def run_ml_inference(self, limit: int = 20) -> dict[str, Any]:
    runtime = get_runtime()
    now = datetime.now(timezone.utc)
    run_key = now.date().isoformat()

    def infer() -> dict[str, Any]:
        service = MLForecastService(cache=runtime.signals.cache, snapshots=runtime.snapshots)
        result = service.infer(limit=limit)
        runtime.events.publish("signals", "ml_predictions.updated", result)
        return {
            "run_key": run_key,
            "predictions_count": result["predictions_count"],
            "status": "complete",
        }

    return _execute_task(self, "ml-inference", run_key, infer)


@app.task(bind=True, name="tasks.retrain_ml_forecast")
def retrain_ml_forecast(self) -> dict[str, Any]:
    runtime = get_runtime()
    now = datetime.now(timezone.utc)
    run_key = now.date().isoformat()

    def train() -> dict[str, Any]:
        service = MLForecastService(cache=runtime.signals.cache, snapshots=runtime.snapshots)
        result = service.train()
        runtime.signals.cache.delete("ml:forecast:v1")
        return {**result, "run_key": run_key, "status": "complete"}

    return _execute_task(self, "ml-retrain", run_key, train)
