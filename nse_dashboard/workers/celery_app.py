from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from nse_dashboard.core.settings import Settings

settings = Settings.from_environment()
broker_url = settings.celery_broker_url or settings.redis_url or "redis://localhost:6379/1"
result_backend = settings.celery_result_backend or settings.redis_url or broker_url

app = Celery(
    "nse_dashboard",
    broker=broker_url,
    backend=result_backend,
    include=[
        "nse_dashboard.workers.tasks",
        "nse_dashboard.tasks.ml_tasks",
        "nse_dashboard.five_percent_strategy.tasks",
    ],
)
app.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.idempotency_ttl_seconds,
    task_routes={
        "workers.run_market_pipeline": {"queue": "ingestion"},
        "workers.ingest_market_data": {"queue": "ingestion"},
        "workers.compute_signals": {"queue": "computation"},
        "workers.persist_snapshot": {"queue": "snapshots"},
        "workers.evaluate_alerts": {"queue": "alerts"},
        "workers.generate_weekly_predictions": {"queue": "computation"},
        "workers.generate_monthly_predictions": {"queue": "computation"},
        "workers.generate_alpha_rankings": {"queue": "computation"},
        "workers.generate_growth_radar": {"queue": "computation"},
        "workers.ingest_growth_features": {"queue": "ingestion"},
        "workers.ingest_filing_document": {"queue": "ingestion"},
        "workers.ingest_fundamental_features": {"queue": "ingestion"},
        "workers.aggregate_sentiment_features": {"queue": "computation"},
        "workers.ingest_legal_risk": {"queue": "ingestion"},
        "tasks.run_ml_inference": {"queue": "computation"},
        "tasks.retrain_ml_forecast": {"queue": "computation"},
        "five_percent_strategy.run_daily_scan": {"queue": "computation"},
        "five_percent_strategy.run_backtest": {"queue": "computation"},
        "five_percent_strategy.update_paper_trades": {"queue": "computation"},
        "five_percent_strategy.publish_latest_signals": {"queue": "computation"},
    },
    beat_schedule={
        "scheduled-market-pipeline": {
            "task": "workers.run_market_pipeline",
            "schedule": settings.worker_schedule_seconds,
        },
        "daily-weekly-predictions": {
            "task": "workers.generate_weekly_predictions",
            "schedule": crontab(hour=16, minute=0, day_of_week="1-5"),
        },
        "scheduled-monthly-predictions": {
            "task": "workers.generate_monthly_predictions",
            "schedule": crontab(hour=16, minute=15, day_of_month="1"),
            "args": (1,),
        },
        "weekly-alpha-rankings": {
            "task": "workers.generate_alpha_rankings",
            "schedule": crontab(hour=16, minute=30, day_of_week="5"),
            "args": ("weekly", 1),
        },
        "monthly-alpha-rankings": {
            "task": "workers.generate_alpha_rankings",
            "schedule": crontab(hour=16, minute=45, day_of_month="1-5"),
            "args": ("monthly", 1),
        },
        "weekly-growth-radar": {
            "task": "workers.generate_growth_radar",
            "schedule": crontab(hour=17, minute=0, day_of_week="5"),
        },
        "daily-ml-forward-returns": {
            "task": "tasks.run_ml_inference",
            "schedule": crontab(hour=8, minute=0),
        },
        "weekly-ml-retrain": {
            "task": "tasks.retrain_ml_forecast",
            "schedule": crontab(hour=6, minute=0, day_of_week="0"),
        },
        "daily-five-percent-strategy-scan": {
            "task": "five_percent_strategy.run_daily_scan",
            "schedule": crontab(hour=16, minute=10, day_of_week="1-5"),
        },
        "five-percent-strategy-paper-trade-updates": {
            "task": "five_percent_strategy.update_paper_trades",
            "schedule": crontab(minute="*/15", hour="9-15", day_of_week="1-5"),
        },
    },
)
