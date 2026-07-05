from __future__ import annotations

from dataclasses import dataclass

from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.snapshots import SnapshotRepository
from nse_dashboard.infrastructure.cache import RedisTtlCache
from nse_dashboard.infrastructure.idempotency import RedisIdempotencyStore
from nse_dashboard.infrastructure.postgres import PostgresSnapshotRepository
from nse_dashboard.infrastructure.yahoo import YahooFinanceAdapter
from nse_dashboard.services.signals import SignalService
from nse_dashboard.services.monthly_predictions import MonthlyPredictionService
from nse_dashboard.services.weekly_predictions import WeeklyPredictionService
from nse_dashboard.services.alpha_ranking import AlphaRankingService
from nse_dashboard.services.growth_radar import GrowthRadarService
from nse_dashboard.streaming.broker import RedisEventPublisher
from nse_dashboard.five_percent_strategy.service import FivePercentStrategyService


@dataclass(slots=True)
class WorkerRuntime:
    settings: Settings
    signals: SignalService
    idempotency: RedisIdempotencyStore
    snapshots: SnapshotRepository
    events: RedisEventPublisher
    weekly_predictions: WeeklyPredictionService
    monthly_predictions: MonthlyPredictionService
    alpha_rankings: AlphaRankingService
    growth_radar: GrowthRadarService
    five_percent_strategy: FivePercentStrategyService


_runtime: WorkerRuntime | None = None


def get_runtime() -> WorkerRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime

    settings = Settings.from_environment()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required by background workers")
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required by snapshot and alert workers")
    cache = RedisTtlCache(settings.redis_url, settings.dependency_timeout_seconds)
    snapshots: SnapshotRepository = PostgresSnapshotRepository(
        settings.database_url, settings.dependency_timeout_seconds
    )
    signals = SignalService(
        adapter=YahooFinanceAdapter(),
        cache=cache,
        snapshots=snapshots,
        cache_seconds=settings.cache_seconds,
        default_period=settings.default_period,
    )
    _runtime = WorkerRuntime(
        settings=settings,
        signals=signals,
        idempotency=RedisIdempotencyStore(
            settings.redis_url, settings.dependency_timeout_seconds
        ),
        snapshots=snapshots,
        events=RedisEventPublisher(
            settings.redis_url,
            settings.dependency_timeout_seconds,
            settings.stream_channel_prefix,
        ),
        weekly_predictions=WeeklyPredictionService(
            adapter=signals.adapter,
            snapshots=snapshots,
        ),
        monthly_predictions=MonthlyPredictionService(
            adapter=signals.adapter,
            snapshots=snapshots,
        ),
        alpha_rankings=AlphaRankingService(snapshots),
        growth_radar=GrowthRadarService(signals.adapter, snapshots),
        five_percent_strategy=FivePercentStrategyService(
            adapter=signals.adapter,
            repository=snapshots,
        ),
    )
    return _runtime
