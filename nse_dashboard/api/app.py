from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import anyio
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nse_dashboard.core.json import json_ready
from nse_dashboard.core.logging import configure_logging
from nse_dashboard.core.observability import ApiMetrics, configure_tracing
from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.market_data import DataSourceError
from nse_dashboard.domain.options import OptionTick
from nse_dashboard.domain.snapshots import NullSnapshotRepository
from nse_dashboard.infrastructure.cache import MemoryTtlCache, RedisTtlCache
from nse_dashboard.infrastructure.postgres import PostgresSnapshotRepository
from nse_dashboard.infrastructure.yahoo import YahooFinanceAdapter
from nse_dashboard.options.analytics import analyze_option_chain
from nse_dashboard.options.smart_money import SMART_MONEY_WEIGHTS, rank_smart_money
from nse_dashboard.api.deps import require_menu
from nse_dashboard.api.routes.admin import create_router as create_admin_router
from nse_dashboard.api.routes.auth import create_router as create_auth_router
from nse_dashboard.api.routes.ml_predictions import create_router as create_ml_predictions_router
from nse_dashboard.five_percent_strategy.routes import create_router as create_five_percent_strategy_router
from nse_dashboard.five_percent_strategy.service import FivePercentStrategyService
from nse_dashboard.infrastructure.auth_repository import AuthRepository
from nse_dashboard.services.signals import SignalService
from nse_dashboard.services.monthly_predictions import MonthlyPredictionService
from nse_dashboard.services.weekly_predictions import WeeklyPredictionService
from nse_dashboard.services.paper_portfolio import PaperPortfolioService
from nse_dashboard.services.alpha_ranking import AlphaRankingService
from nse_dashboard.services.stock_analysis import SingleStockAnalysisService
from nse_dashboard.services.growth_radar import GrowthRadarService
from nse_dashboard.services.quotes import StockQuoteService
from nse_dashboard.streaming.broker import (
    STREAM_CHANNELS,
    EventBroker,
    MemoryEventBroker,
    RedisEventBroker,
)

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("nse_dashboard.api")


def create_app(
    settings: Settings | None = None,
    service: SignalService | None = None,
    stream_broker: EventBroker | None = None,
) -> FastAPI:
    settings = settings or Settings.from_environment()
    configure_logging(settings.log_level)
    if service is None:
        cache = (
            RedisTtlCache(settings.redis_url, settings.dependency_timeout_seconds)
            if settings.redis_url
            else MemoryTtlCache()
        )
        snapshots = (
            PostgresSnapshotRepository(settings.database_url, settings.dependency_timeout_seconds)
            if settings.database_url
            else NullSnapshotRepository()
        )
        service = SignalService(
            adapter=YahooFinanceAdapter(),
            cache=cache,
            snapshots=snapshots,
            cache_seconds=settings.cache_seconds,
            default_period=settings.default_period,
        )
    auth_repo = (
        AuthRepository(settings.database_url, settings.dependency_timeout_seconds)
        if settings.database_url
        else None
    )

    def menu_dependencies(menu_key: str) -> list:
        return [Depends(require_menu(menu_key))] if auth_repo is not None else []
    if stream_broker is None:
        stream_broker = (
            RedisEventBroker(
                settings.redis_url,
                settings.dependency_timeout_seconds,
                settings.stream_channel_prefix,
            )
            if settings.redis_url
            else MemoryEventBroker()
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if auth_repo is not None and settings.admin_bootstrap_password:
            await run_in_threadpool(
                auth_repo.ensure_admin,
                settings.admin_bootstrap_username,
                settings.admin_bootstrap_password,
            )
        yield
        await stream_broker.close()
        service.cache.close()
        service.snapshots.close()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.signal_service = service
    app.state.auth_repository = auth_repo
    weekly_predictions = WeeklyPredictionService(
        adapter=service.adapter,
        snapshots=service.snapshots,
    )
    app.state.weekly_prediction_service = weekly_predictions
    monthly_predictions = MonthlyPredictionService(
        adapter=service.adapter,
        snapshots=service.snapshots,
    )
    app.state.monthly_prediction_service = monthly_predictions
    paper_portfolio = PaperPortfolioService(service.snapshots)
    app.state.paper_portfolio_service = paper_portfolio
    alpha_rankings = AlphaRankingService(service.snapshots)
    app.state.alpha_ranking_service = alpha_rankings
    stock_analysis = SingleStockAnalysisService(
        service.adapter, service.snapshots, cache=service.cache, period="max"
    )
    app.state.stock_analysis_service = stock_analysis
    quote_service = StockQuoteService(
        service.adapter, service.snapshots, cache=service.cache
    )
    app.state.quote_service = quote_service
    growth_radar = GrowthRadarService(service.adapter, service.snapshots)
    app.state.growth_radar_service = growth_radar
    app.state.stream_broker = stream_broker
    app.state.metrics = ApiMetrics()
    five_percent_strategy = FivePercentStrategyService(
        adapter=service.adapter,
        repository=service.snapshots,
    )
    app.state.five_percent_strategy_service = five_percent_strategy
    app.include_router(
        create_ml_predictions_router(
            settings, service.cache, service.snapshots, menu_dependencies("future")
        )
    )
    app.include_router(
        create_five_percent_strategy_router(
            settings,
            five_percent_strategy,
            stream_broker,
            menu_dependencies("five_percent_strategy"),
            metrics=app.state.metrics,
        )
    )
    if auth_repo is not None:
        app.include_router(create_auth_router(settings, auth_repo))
        app.include_router(create_admin_router(auth_repo))

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Accept", "Content-Type", "X-Request-ID"],
        )

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:128]
        started = perf_counter()
        response = await call_next(request)
        duration_ms = round((perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        if settings.metrics_enabled and request.url.path != "/metrics":
            app.state.metrics.observe_request(
                request.method, route_path, response.status_code, duration_ms / 1000
            )
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    configure_tracing(app, settings)

    @app.exception_handler(DataSourceError)
    async def data_source_error(_: Request, exc: DataSourceError) -> JSONResponse:
        logger.warning("market data provider failed: %s", exc)
        return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="dashboard.html")

    @app.get("/health/live", tags=["operations"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["operations"])
    async def ready() -> Any:
        dependencies: dict[str, str] = {}
        for name, dependency in (
            ("cache", service.cache),
            ("database", service.snapshots),
        ):
            try:
                healthy = await run_in_threadpool(dependency.ping)
                dependencies[name] = "ok" if healthy else "unavailable"
            except Exception:
                logger.exception("readiness dependency failed", extra={"dependency": name})
                dependencies[name] = "unavailable"
        if "unavailable" in dependencies.values():
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "dependencies": dependencies},
            )
        return {
            "status": "ready",
            "market_data_adapter": service.adapter.name,
            "dependencies": dependencies,
        }

    @app.get("/metrics", tags=["operations"], include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="Metrics are disabled")
        return PlainTextResponse(
            app.state.metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/dashboard", tags=["compatibility"])
    @app.get("/api/v1/dashboard", tags=["signals"], dependencies=menu_dependencies("signals"))
    async def market_dashboard(refresh: bool = Query(default=False)):
        if refresh and _can_queue_background_work(settings):
            from nse_dashboard.workers.tasks import run_market_pipeline as task

            queued = task.apply_async(queue="ingestion")
            cached_dashboard = service.cache.get(service.dashboard_cache_key)
            latest = dict(cached_dashboard) if cached_dashboard else {
                "generated_at": None,
                "market_date": None,
                "source": service.adapter.name,
                "strategy": service.strategy.name,
                "regime": {
                    "state": "QUEUED",
                    "maximum_exposure_pct": 0,
                    "risk_per_trade_pct": 0,
                },
                "universe_size": 0,
                "stocks_scored": 0,
                "failures": [],
                "sectors": [],
            }
            latest["generation_status"] = {
                "state": "queued",
                "task_id": queued.id,
                "message": "Market refresh was queued; showing the latest cached dashboard.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        return await run_in_threadpool(service.scan_market, refresh)

    @app.get("/api/signal", tags=["compatibility"])
    async def signal_compatibility(symbol: str = "RELIANCE.NS"):
        try:
            return await run_in_threadpool(service.evaluate, symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/signals/{symbol}", tags=["signals"])
    async def signal(symbol: str):
        try:
            return await run_in_threadpool(service.evaluate, symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/signals/{symbol}/history", tags=["signals"])
    async def signal_history(symbol: str, limit: int = Query(default=100, ge=1, le=1000)):
        try:
            return await run_in_threadpool(service.history, symbol, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/alerts", tags=["alerts"])
    async def recent_alerts(limit: int = Query(default=100, ge=1, le=1000)):
        return await run_in_threadpool(service.snapshots.recent_alerts, limit)

    @app.get("/api/v1/stock-prices", tags=["market data"])
    async def stock_prices(symbols: str | None = Query(default=None)):
        try:
            return await run_in_threadpool(quote_service.prices, symbols)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/weekly-predictions",
        tags=["weekly predictions"],
        dependencies=menu_dependencies("weekly"),
    )
    async def latest_weekly_predictions(
        max_price: float | None = Query(default=None, gt=0),
        limit_per_sector: int = Query(default=5, ge=1, le=20),
    ):
        return await run_in_threadpool(
            weekly_predictions.latest, max_price, limit_per_sector
        )

    @app.get(
        "/api/v1/weekly-predictions/{symbol}/history",
        tags=["weekly predictions"],
    )
    async def weekly_prediction_history(
        symbol: str, limit: int = Query(default=100, ge=1, le=1000)
    ):
        return await run_in_threadpool(weekly_predictions.history, symbol, limit)

    @app.post("/api/v1/weekly-predictions/generate", tags=["weekly predictions"])
    async def generate_weekly_predictions(
        min_price: float | None = Query(default=None, ge=0),
        max_price: float | None = Query(default=None, gt=0),
        min_probability: float = Query(default=0.60, ge=0, le=1),
        min_expected_return: float = Query(default=2.0, ge=-100, le=100),
        min_average_traded_value: float = Query(default=10_000_000, ge=0),
        limit_per_sector: int = Query(default=5, ge=1, le=20),
    ):
        if _can_queue_background_work(settings):
            from nse_dashboard.workers.tasks import generate_weekly_predictions as task

            queued = task.apply_async(
                kwargs={
                    "min_price": min_price,
                    "max_price": max_price,
                    "min_probability": min_probability,
                    "min_expected_return": min_expected_return,
                    "min_average_traded_value": min_average_traded_value,
                    "limit_per_sector": limit_per_sector,
                },
                queue="computation",
            )
            latest = dict(
                await run_in_threadpool(
                    weekly_predictions.latest, max_price, limit_per_sector
                )
            )
            latest["generation_status"] = {
                "state": "queued",
                "task_id": queued.id,
                "message": "Weekly prediction generation was queued; showing the latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        try:
            result = await _run_generation_with_timeout(
                lambda: weekly_predictions.generate(
                    min_price=min_price,
                    max_price=max_price,
                    min_probability=min_probability,
                    min_expected_return=min_expected_return,
                    min_average_traded_value=min_average_traded_value,
                    limit_per_sector=limit_per_sector,
                ),
                (
                    None
                    if settings.environment == "test"
                    else settings.prediction_generate_timeout_seconds
                ),
            )
            await stream_broker.publish(
                "signals", "weekly_predictions.updated", result
            )
            return result
        except TimeoutError:
            logger.warning("weekly prediction generation timed out; returning latest persisted result")
            latest = await run_in_threadpool(
                weekly_predictions.latest, max_price, limit_per_sector
            )
            latest["generation_status"] = {
                "state": "running",
                "message": "Generation is still running; returning latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            logger.exception("weekly prediction generation failed; returning latest persisted result")
            latest = await run_in_threadpool(
                weekly_predictions.latest, max_price, limit_per_sector
            )
            latest["generation_status"] = {
                "state": "fallback",
                "message": "Generation failed; returning latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))

    @app.get(
        "/api/v1/monthly-predictions",
        tags=["monthly predictions"],
        dependencies=menu_dependencies("monthly"),
    )
    async def latest_monthly_predictions(
        horizon_months: int = Query(default=1, ge=1, le=12),
        max_price: float | None = Query(default=None, gt=0),
        limit_per_sector: int = Query(default=5, ge=1, le=20),
    ):
        return await run_in_threadpool(
            monthly_predictions.latest,
            horizon_months,
            max_price,
            limit_per_sector,
        )

    @app.get(
        "/api/v1/monthly-predictions/{symbol}/history",
        tags=["monthly predictions"],
    )
    async def monthly_prediction_history(
        symbol: str,
        horizon_months: int | None = Query(default=None, ge=1, le=12),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        return await run_in_threadpool(
            monthly_predictions.history, symbol, horizon_months, limit
        )

    @app.post("/api/v1/monthly-predictions/generate", tags=["monthly predictions"])
    async def generate_monthly_predictions(
        horizon_months: int = Query(default=1, ge=1, le=12),
        max_price: float | None = Query(default=None, gt=0),
        min_score: float = Query(default=60, ge=0, le=100),
        min_average_traded_value: float = Query(default=10_000_000, ge=0),
        limit_per_sector: int = Query(default=5, ge=1, le=20),
    ):
        if _can_queue_background_work(settings):
            from nse_dashboard.workers.tasks import generate_monthly_predictions as task

            queued = task.apply_async(
                kwargs={
                    "horizon_months": horizon_months,
                    "max_price": max_price,
                    "min_score": min_score,
                    "min_average_traded_value": min_average_traded_value,
                    "limit_per_sector": limit_per_sector,
                },
                queue="computation",
            )
            latest = dict(
                await run_in_threadpool(
                    monthly_predictions.latest,
                    horizon_months,
                    max_price,
                    limit_per_sector,
                )
            )
            latest["generation_status"] = {
                "state": "queued",
                "task_id": queued.id,
                "message": "Monthly prediction generation was queued; showing the latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        try:
            result = await _run_generation_with_timeout(
                lambda: monthly_predictions.generate(
                    horizon_months,
                    max_price=max_price,
                    min_score=min_score,
                    min_average_traded_value=min_average_traded_value,
                    limit_per_sector=limit_per_sector,
                ),
                (
                    None
                    if settings.environment == "test"
                    else settings.prediction_generate_timeout_seconds
                ),
            )
            await stream_broker.publish(
                "signals", "monthly_predictions.updated", result
            )
            return result
        except TimeoutError:
            logger.warning("monthly prediction generation timed out; returning latest persisted result")
            latest = await run_in_threadpool(
                monthly_predictions.latest,
                horizon_months,
                max_price,
                limit_per_sector,
            )
            latest["generation_status"] = {
                "state": "running",
                "message": "Generation is still running; returning latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            logger.exception("monthly prediction generation failed; returning latest persisted result")
            latest = await run_in_threadpool(
                monthly_predictions.latest,
                horizon_months,
                max_price,
                limit_per_sector,
            )
            latest["generation_status"] = {
                "state": "fallback",
                "message": "Generation failed; returning latest persisted predictions.",
            }
            return JSONResponse(status_code=202, content=json_ready(latest))

    @app.get("/api/rankings/weekly", tags=["alpha rankings"])
    async def latest_weekly_alpha_rankings(
        fundamental_grade: str | None = Query(default=None),
        exclude_legal_risks: bool = Query(default=False),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        grades = _parse_fundamental_grades(fundamental_grade)
        return await run_in_threadpool(
            lambda: alpha_rankings.latest(
                "weekly",
                limit=limit,
                fundamental_grades=grades,
                exclude_legal_risks=exclude_legal_risks,
            )
        )

    @app.post("/api/rankings/weekly/generate", tags=["alpha rankings"])
    async def generate_weekly_alpha_rankings():
        result = await run_in_threadpool(alpha_rankings.generate, "weekly", 1)
        await stream_broker.publish("signals", "weekly_alpha.updated", result)
        return result

    @app.get("/api/rankings/monthly", tags=["alpha rankings"])
    async def latest_monthly_alpha_rankings(
        horizon_months: int = Query(default=1, ge=1, le=12),
        fundamental_grade: str | None = Query(default=None),
        exclude_legal_risks: bool = Query(default=False),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        grades = _parse_fundamental_grades(fundamental_grade)
        return await run_in_threadpool(
            lambda: alpha_rankings.latest(
                "monthly",
                horizon_months,
                limit=limit,
                fundamental_grades=grades,
                exclude_legal_risks=exclude_legal_risks,
            )
        )

    @app.post("/api/rankings/monthly/generate", tags=["alpha rankings"])
    async def generate_monthly_alpha_rankings(
        horizon_months: int = Query(default=1, ge=1, le=12),
    ):
        result = await run_in_threadpool(
            alpha_rankings.generate, "monthly", horizon_months
        )
        await stream_broker.publish("signals", "monthly_alpha.updated", result)
        return result

    @app.get(
        "/api/v1/growth-radar",
        tags=["growth radar"],
        dependencies=menu_dependencies("radar"),
    )
    async def latest_growth_radar(
        sector: str | None = Query(default=None),
        state: str | None = Query(default=None),
        algorithm: str | None = Query(default=None),
        track: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ):
        allowed_states = {
            "EARLY_WATCH",
            "BUILDING_STRENGTH",
            "QUALIFIED",
            "BREAKOUT_CONFIRMED",
            "REJECTED",
        }
        allowed_algorithms = {
            "earnings_inflection",
            "order_book_capex",
            "turnaround_deleveraging",
            "price_volume_accumulation",
            "valuation",
            "ownership",
            "catalyst",
        }
        allowed_tracks = {"compounder_12m", "multibagger_24m"}
        if state and state not in allowed_states:
            raise HTTPException(status_code=400, detail="Invalid growth-radar state")
        if algorithm and algorithm not in allowed_algorithms:
            raise HTTPException(status_code=400, detail="Invalid growth-radar algorithm")
        if track and track not in allowed_tracks:
            raise HTTPException(status_code=400, detail="Invalid growth-radar track")
        return await run_in_threadpool(
            lambda: growth_radar.latest(
                sector=sector,
                state=state,
                algorithm=algorithm,
                track=track,
                limit=limit,
            )
        )

    @app.get("/api/v1/growth-radar/{symbol}/projections", tags=["growth radar"])
    async def growth_radar_projections(symbol: str):
        item = await run_in_threadpool(growth_radar.detail, symbol)
        if item is None:
            raise HTTPException(status_code=404, detail="Growth-radar symbol not found")
        return {
            "symbol": item["symbol"],
            "as_of": item["as_of"],
            "current_price": item["current_price"],
            "confidence_pct": item["confidence_pct"],
            "projections": item["projections"],
            "disclaimer": (
                "Scenario prices are research estimates, not assured targets "
                "or investment advice."
            ),
        }

    @app.get("/api/v1/growth-radar/{symbol}", tags=["growth radar"])
    async def growth_radar_detail(symbol: str):
        item = await run_in_threadpool(growth_radar.detail, symbol)
        if item is None:
            raise HTTPException(status_code=404, detail="Growth-radar symbol not found")
        return item

    @app.post("/api/v1/growth-radar/generate", tags=["growth radar"])
    async def generate_growth_radar():
        if settings.celery_broker_url or settings.redis_url:
            from nse_dashboard.workers.tasks import generate_growth_radar as task

            queued = task.apply_async(queue="computation")
            return JSONResponse(
                status_code=202,
                content={
                    "generation_status": "queued",
                    "task_id": queued.id,
                    "message": "Growth-radar generation was queued.",
                },
            )
        result = await run_in_threadpool(growth_radar.generate)
        await stream_broker.publish("signals", "growth_radar.updated", result)
        return JSONResponse(
            status_code=202,
            content={
                **json_ready(result),
                "generation_status": "complete",
                "message": "Generated inline because no Celery broker is configured.",
            },
        )

    @app.get("/api/v1/analysis/{symbol}", tags=["stock analysis"])
    @app.get("/api/analysis/{symbol}", tags=["stock analysis"])
    async def analyze_single_stock(symbol: str):
        try:
            return await run_in_threadpool(stock_analysis.analyze, symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/analysis/stock/{symbol}",
        tags=["stock analysis"],
        dependencies=menu_dependencies("analysis"),
    )
    async def analyze_stock_deep_dive(
        symbol: str,
        horizon: str = Query(default="15d"),
        force_refresh: bool = Query(default=False),
    ):
        try:
            return await run_in_threadpool(
                lambda: stock_analysis.deep_dive(
                    symbol, horizon=horizon, force_refresh=force_refresh
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/paper-portfolio", tags=["paper portfolio"])
    async def get_paper_portfolio():
        return await run_in_threadpool(paper_portfolio.get)

    @app.post("/api/v1/paper-portfolio/positions", tags=["paper portfolio"])
    async def open_paper_position(payload: dict[str, Any] = Body(...)):
        try:
            return await run_in_threadpool(paper_portfolio.open_position, payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/paper-portfolio/positions/{symbol}/exit", tags=["paper portfolio"])
    async def exit_paper_position(symbol: str, payload: dict[str, Any] = Body(...)):
        try:
            return await run_in_threadpool(
                paper_portfolio.apply_exit, symbol, float(payload["price"]), payload.get("quantity")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/paper-portfolio/positions/{symbol}/evaluate", tags=["paper portfolio"])
    async def evaluate_paper_position(symbol: str, payload: dict[str, Any] = Body(...)):
        try:
            return await run_in_threadpool(paper_portfolio.evaluate_position, symbol, payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/paper-portfolio/month-review", tags=["paper portfolio"])
    async def resume_paper_portfolio():
        return await run_in_threadpool(paper_portfolio.resume_at_month_review)

    @app.post("/api/v1/options/analytics", tags=["options"])
    async def option_analytics(
        ticks: list[dict[str, Any]] = Body(...),
        risk_free_rate: float = Query(default=0.07, gt=-1, lt=1),
        dividend_yield: float = Query(default=0.0, gt=-1, lt=1),
    ):
        try:
            chain = [_parse_option_tick(item) for item in ticks]
            result = await run_in_threadpool(
                analyze_option_chain, chain, risk_free_rate, dividend_yield
            )
            await stream_broker.publish("options", "options.analytics", result)
            return result
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/options/smart-money", tags=["options"])
    async def smart_money_ranking(
        ticks: list[dict[str, Any]] = Body(...),
        risk_free_rate: float = Query(default=0.07, gt=-1, lt=1),
        dividend_yield: float = Query(default=0.0, gt=-1, lt=1),
        lookback_days: int = Query(default=20, ge=2, le=60),
    ):
        try:
            history = [_parse_option_tick(item) for item in ticks]
            ranking = await run_in_threadpool(
                rank_smart_money,
                history,
                risk_free_rate,
                dividend_yield,
                lookback_days,
            )
            result = {
                "underlying": history[0].underlying,
                "as_of": max(item.timestamp for item in history).isoformat(),
                "lookback_days": lookback_days,
                "weights": SMART_MONEY_WEIGHTS,
                "ranking": ranking,
            }
            saver = getattr(service.snapshots, "save_options_features", None)
            if saver and ranking:
                top = ranking[0]
                await run_in_threadpool(
                    saver,
                    {
                        "symbol": history[0].underlying.upper(),
                        "as_of": result["as_of"],
                        "model_version": "smart-money-v1",
                        "score": top["smart_money_score"],
                        "coverage": "FULL",
                        "features": {
                            "top_contract": top["symbol"],
                            "sub_scores": top["sub_scores"],
                            "raw_factors": top["raw_factors"],
                            "lookback_days": lookback_days,
                        },
                        "contributions": top["sub_scores"],
                    },
                )
            await stream_broker.publish("options", "options.smart_money", result)
            return result
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/api/v1/stream/{channel}")
    async def stream(websocket: WebSocket, channel: str) -> None:
        if channel not in STREAM_CHANNELS:
            await websocket.close(code=4404, reason="Unknown stream channel")
            return
        token = _websocket_token(websocket)
        if not token or not any(
            secrets.compare_digest(token, expected) for expected in settings.websocket_tokens
        ):
            await websocket.close(code=4401, reason="Invalid or missing bearer token")
            return

        await websocket.accept()
        app.state.metrics.websocket_opened(channel)
        try:
            async with stream_broker.subscribe(channel) as messages:
                iterator = messages.__aiter__()
                next_message = asyncio.create_task(anext(iterator))
                receive = asyncio.create_task(websocket.receive())
                try:
                    while True:
                        done, _ = await asyncio.wait(
                            {next_message, receive},
                            timeout=settings.websocket_heartbeat_seconds,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if receive in done:
                            client_message = receive.result()
                            if client_message["type"] == "websocket.disconnect":
                                return
                            receive = asyncio.create_task(websocket.receive())
                        if next_message in done:
                            await websocket.send_json(next_message.result())
                            next_message = asyncio.create_task(anext(iterator))
                        if not done:
                            await websocket.send_json(
                                {"type": "heartbeat", "channel": channel}
                            )
                finally:
                    next_message.cancel()
                    receive.cancel()
                    await asyncio.gather(next_message, receive, return_exceptions=True)
        except (asyncio.CancelledError, WebSocketDisconnect, StopAsyncIteration):
            return
        finally:
            app.state.metrics.websocket_closed(channel)

    return app


def _websocket_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() == "bearer" and credentials:
        return credentials
    return websocket.query_params.get("token")


def _can_queue_background_work(settings: Settings) -> bool:
    return settings.environment != "test" and bool(
        settings.celery_broker_url or settings.redis_url
    )


async def _run_generation_with_timeout(
    operation: Callable[[], dict[str, Any]], timeout_seconds: float | None
) -> dict[str, Any]:
    if timeout_seconds is None:
        return await anyio.to_thread.run_sync(operation)
    with anyio.fail_after(timeout_seconds):
        return await anyio.to_thread.run_sync(operation, abandon_on_cancel=True)


def _parse_option_tick(values: dict[str, Any]) -> OptionTick:
    """Convert transport values while keeping the domain model provider-neutral."""
    data = dict(values)
    for name in ("strike", "spot_price", "option_price", "implied_volatility", "bid", "ask"):
        if data.get(name) is not None:
            data[name] = float(data[name])
    for name in ("open_interest", "volume", "previous_open_interest", "lot_size"):
        if data.get(name) is not None:
            data[name] = int(data[name])
    from datetime import datetime

    for name in ("expiry", "timestamp"):
        value = data.get(name)
        if isinstance(value, str):
            data[name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return OptionTick(**data)


def _parse_fundamental_grades(value: str | None) -> set[str] | None:
    if value is None or not value.strip():
        return None
    grades = {item.strip().upper() for item in value.split(",") if item.strip()}
    invalid = grades - {"A", "B", "C", "D", "F"}
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid fundamental grades: {', '.join(sorted(invalid))}",
        )
    return grades
