from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from nse_dashboard.core.json import json_ready
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import TtlCache
from nse_dashboard.services.ml_forecast_service import MLForecastService


def create_router(
    settings: Settings,
    cache: TtlCache,
    snapshots: Any,
    dependencies: list | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ml", tags=["ml predictions"])
    service = MLForecastService(cache=cache, snapshots=snapshots)

    @router.get("/forward-returns", dependencies=dependencies or [])
    async def forward_returns(
        limit: int = Query(default=20, ge=1, le=100),
        refresh: bool = Query(default=False),
    ):
        if refresh and settings.environment != "test" and (
            settings.celery_broker_url or settings.redis_url
        ):
            from nse_dashboard.tasks.ml_tasks import run_ml_inference

            queued = run_ml_inference.apply_async(kwargs={"limit": limit}, queue="computation")
            latest = await run_in_threadpool(service.latest, limit)
            latest["generation_status"] = {
                "state": "queued",
                "task_id": queued.id,
                "message": "ML inference was queued; showing the latest cached forecast.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        if refresh:
            result = await run_in_threadpool(service.infer, None, limit)
            result["generation_status"] = {
                "state": "complete",
                "message": "Generated inline because no Celery broker is configured.",
            }
            return JSONResponse(status_code=202, content=json_ready(result))
        return await run_in_threadpool(service.latest, limit)

    return router
