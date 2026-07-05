from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from nse_dashboard.core.json import json_ready
from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.market_data import DataSourceError
from nse_dashboard.five_percent_strategy.schemas import (
    BacktestRequest,
    ClosePaperTradeRequest,
    GenerateScanRequest,
    ProjectionRequest,
    StartPaperTradeRequest,
)
from nse_dashboard.five_percent_strategy.service import FivePercentStrategyService


def create_router(
    settings: Settings,
    service: FivePercentStrategyService,
    stream_broker: Any,
    dependencies: list | None = None,
    metrics: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/five-percent-strategy", tags=["five percent strategy"])
    deps = dependencies or []

    @router.post("/generate", dependencies=deps)
    async def generate(request: GenerateScanRequest):
        if settings.environment != "test" and (settings.celery_broker_url or settings.redis_url):
            from nse_dashboard.five_percent_strategy.tasks import run_daily_scan

            queued = run_daily_scan.apply_async(kwargs=request.model_dump(), queue="computation")
            latest = dict(await run_in_threadpool(service.latest))
            latest["generation_status"] = {
                "state": "queued",
                "task_id": queued.id,
                "message": "Scan was queued; showing the latest persisted scan.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        started = perf_counter()
        try:
            result = await run_in_threadpool(service.generate, **request.model_dump())
        except DataSourceError as exc:
            if metrics is not None:
                metrics.increment("nse_five_percent_strategy_api_errors_total")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if metrics is not None:
            metrics.observe_duration("nse_five_percent_strategy_scan_duration_seconds", perf_counter() - started)
            metrics.increment("nse_five_percent_strategy_symbols_scanned_total", result["universe_size"])
            metrics.increment("nse_five_percent_strategy_candidates_generated_total", result["candidates_count"])
        await stream_broker.publish("signals", "five_percent_strategy.scan_completed", result)
        return json_ready(result)

    @router.get("/latest", dependencies=deps)
    async def latest():
        return await run_in_threadpool(service.latest)

    @router.get("/runs/{run_id}", dependencies=deps)
    async def run_by_id(run_id: str):
        result = await run_in_threadpool(service.run_by_id, run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @router.get("/{symbol}/history", dependencies=deps)
    async def symbol_history(symbol: str, limit: int = Query(default=100, ge=1, le=1000)):
        return await run_in_threadpool(service.symbol_history, symbol, limit)

    @router.post("/backtest", dependencies=deps)
    async def backtest(request: BacktestRequest):
        started = perf_counter()
        try:
            result = await run_in_threadpool(service.backtest, **request.model_dump())
        except DataSourceError as exc:
            if metrics is not None:
                metrics.increment("nse_five_percent_strategy_api_errors_total")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if metrics is not None:
            metrics.observe_duration("nse_five_percent_strategy_backtest_duration_seconds", perf_counter() - started)
            metrics.increment("nse_five_percent_strategy_backtest_trades_total", result["total_trades"])
        await stream_broker.publish("signals", "five_percent_strategy.backtest_completed", result)
        return json_ready(result)

    @router.post("/projection", dependencies=deps)
    async def projection(request: ProjectionRequest):
        return await run_in_threadpool(service.project_compounding, **request.model_dump())

    @router.post("/paper-trades/start", dependencies=deps)
    async def start_paper_trade(request: StartPaperTradeRequest):
        return await run_in_threadpool(service.start_paper_trade, **request.model_dump())

    @router.get("/paper-trades", dependencies=deps)
    async def list_paper_trades(status: str | None = Query(default=None)):
        return await run_in_threadpool(service.list_paper_trades, status)

    @router.get("/paper-trades/{trade_id}", dependencies=deps)
    async def get_paper_trade(trade_id: int):
        result = await run_in_threadpool(service.get_paper_trade, trade_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Paper trade not found")
        return result

    @router.post("/paper-trades/{trade_id}/close", dependencies=deps)
    async def close_paper_trade(trade_id: int, request: ClosePaperTradeRequest):
        result = await run_in_threadpool(
            service.close_paper_trade,
            trade_id,
            exit_price=request.exit_price,
            exit_reason=request.exit_reason,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Paper trade not found")
        event_type = (
            "five_percent_strategy.paper_trade_target_hit"
            if request.exit_reason == "target_hit"
            else "five_percent_strategy.paper_trade_stop_hit"
            if request.exit_reason == "stop_hit"
            else "five_percent_strategy.paper_trade_closed"
        )
        await stream_broker.publish("signals", event_type, result)
        return result

    return router
