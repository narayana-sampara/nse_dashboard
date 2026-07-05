from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from celery import chain

from nse_dashboard.infrastructure.idempotency import TaskAlreadyRunning, execute_once
from nse_dashboard.workers.celery_app import app
from nse_dashboard.workers.runtime import get_runtime
from nse_dashboard.workers.serialization import deserialize_histories, serialize_histories
from nse_dashboard.domain.alpha import normalize_symbol
from nse_dashboard.services.fundamentals import FundamentalService
from nse_dashboard.services.legal_risk import LegalRiskService
from nse_dashboard.services.sentiment import SentimentService
from nse_dashboard.services.growth_radar import FEATURE_SET_VERSION


def scheduled_run_key(now: datetime, interval_seconds: int) -> str:
    bucket = int(now.timestamp()) // interval_seconds
    return f"market:{bucket}"


def weekly_run_key(now: datetime) -> str:
    return f"weekly:{now.date().isoformat()}"


def _parameter_digest(parameters: dict[str, Any]) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _weekly_prediction_run_key(now: datetime, parameters: dict[str, Any]) -> str:
    defaults = {
        "min_price": None,
        "max_price": None,
        "min_probability": 0.60,
        "min_expected_return": 2.0,
        "min_average_traded_value": 10_000_000,
        "limit_per_sector": 5,
    }
    base = weekly_run_key(now)
    return base if parameters == defaults else f"{base}:{_parameter_digest(parameters)}"


def _monthly_prediction_run_key(
    now: datetime, horizon_months: int, parameters: dict[str, Any]
) -> str:
    defaults = {
        "max_price": None,
        "min_score": 60,
        "min_average_traded_value": 10_000_000,
        "limit_per_sector": 5,
    }
    base = f"monthly:{horizon_months}:{now.date().isoformat()}"
    return base if parameters == defaults else f"{base}:{_parameter_digest(parameters)}"


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
        raise task.retry(exc=exc, countdown=30, max_retries=3) from exc


@app.task(bind=True, name="workers.run_market_pipeline")
def run_market_pipeline(self) -> dict[str, Any]:
    runtime = get_runtime()
    run_key = scheduled_run_key(datetime.now(timezone.utc), runtime.settings.worker_schedule_seconds)

    def dispatch() -> dict[str, Any]:
        workflow = chain(
            ingest_market_data.s(run_key).set(queue="ingestion"),
            compute_signals.s().set(queue="computation"),
            persist_snapshot.s().set(queue="snapshots"),
            evaluate_alerts.s().set(queue="alerts"),
        )
        result = workflow.apply_async()
        return {"run_key": run_key, "workflow_id": result.id, "status": "scheduled"}

    return _execute_task(self, "pipeline", run_key, dispatch)


@app.task(bind=True, name="workers.ingest_market_data")
def ingest_market_data(self, run_key: str) -> dict[str, str]:
    runtime = get_runtime()

    def ingest() -> dict[str, str]:
        batch_key = f"workers:data:{run_key}"
        histories = runtime.signals.ingest_market()
        runtime.signals.cache.set(
            batch_key,
            serialize_histories(histories),
            runtime.settings.worker_data_ttl_seconds,
        )
        return {"run_key": run_key, "batch_key": batch_key}

    return _execute_task(self, "ingestion", run_key, ingest)


@app.task(bind=True, name="workers.compute_signals")
def compute_signals(self, context: dict[str, str]) -> dict[str, str]:
    runtime = get_runtime()
    run_key = context["run_key"]

    def compute() -> dict[str, str]:
        payload = runtime.signals.cache.get(context["batch_key"])
        if payload is None:
            raise RuntimeError(f"Market-data batch expired: {context['batch_key']}")
        scan = runtime.signals.compute_market_scan(deserialize_histories(payload))
        scan_key = f"workers:scan:{run_key}"
        runtime.signals.cache.set(
            scan_key,
            scan,
            runtime.settings.worker_data_ttl_seconds,
        )
        runtime.signals.cache.set(
            runtime.signals.dashboard_cache_key,
            scan,
            runtime.settings.cache_seconds,
        )
        runtime.events.publish("signals", "signals.updated", scan)
        return {**context, "scan_key": scan_key}

    return _execute_task(self, "computation", run_key, compute)


@app.task(bind=True, name="workers.persist_snapshot")
def persist_snapshot(self, context: dict[str, str]) -> dict[str, str]:
    runtime = get_runtime()
    run_key = context["run_key"]

    def persist() -> dict[str, str]:
        scan = runtime.signals.cache.get(context["scan_key"])
        if scan is None:
            raise RuntimeError(f"Computed scan expired: {context['scan_key']}")
        runtime.snapshots.save_market_scan(scan, idempotency_key=run_key)
        return context

    return _execute_task(self, "snapshot", run_key, persist)


@app.task(bind=True, name="workers.evaluate_alerts")
def evaluate_alerts(self, context: dict[str, str]) -> dict[str, Any]:
    runtime = get_runtime()
    run_key = context["run_key"]

    def create_alerts() -> dict[str, Any]:
        scan = runtime.signals.cache.get(context["scan_key"])
        if scan is None:
            raise RuntimeError(f"Computed scan expired: {context['scan_key']}")
        count = runtime.snapshots.save_alerts(scan, idempotency_key=run_key)
        runtime.events.publish(
            "alerts",
            "alerts.updated",
            {"run_key": run_key, "alerts_created": count},
        )
        return {"run_key": run_key, "alerts_created": count, "status": "complete"}

    return _execute_task(self, "alerts", run_key, create_alerts)


@app.task(bind=True, name="workers.generate_weekly_predictions")
def generate_weekly_predictions(
    self,
    min_price: float | None = None,
    max_price: float | None = None,
    min_probability: float = 0.60,
    min_expected_return: float = 2.0,
    min_average_traded_value: float = 10_000_000,
    limit_per_sector: int = 5,
) -> dict[str, Any]:
    runtime = get_runtime()
    parameters = {
        "min_price": min_price,
        "max_price": max_price,
        "min_probability": min_probability,
        "min_expected_return": min_expected_return,
        "min_average_traded_value": min_average_traded_value,
        "limit_per_sector": limit_per_sector,
    }
    run_key = _weekly_prediction_run_key(datetime.now(timezone.utc), parameters)

    def generate() -> dict[str, Any]:
        result = runtime.weekly_predictions.generate(**parameters)
        runtime.events.publish(
            "signals", "weekly_predictions.updated", result
        )
        return {
            "run_key": run_key,
            "predictions_count": result["predictions_count"],
            "status": "complete",
        }

    return _execute_task(self, "weekly-predictions", run_key, generate)


@app.task(bind=True, name="workers.generate_monthly_predictions")
def generate_monthly_predictions(
    self,
    horizon_months: int = 1,
    max_price: float | None = None,
    min_score: float = 60,
    min_average_traded_value: float = 10_000_000,
    limit_per_sector: int = 5,
) -> dict[str, Any]:
    runtime = get_runtime()
    now = datetime.now(timezone.utc)
    parameters = {
        "max_price": max_price,
        "min_score": min_score,
        "min_average_traded_value": min_average_traded_value,
        "limit_per_sector": limit_per_sector,
    }
    run_key = _monthly_prediction_run_key(now, horizon_months, parameters)

    def generate() -> dict[str, Any]:
        result = runtime.monthly_predictions.generate(horizon_months, **parameters)
        runtime.events.publish(
            "signals", "monthly_predictions.updated", result
        )
        return {
            "run_key": run_key,
            "horizon_months": horizon_months,
            "predictions_count": result["predictions_count"],
            "status": "complete",
        }

    return _execute_task(self, "monthly-predictions", run_key, generate)


@app.task(bind=True, name="workers.generate_alpha_rankings")
def generate_alpha_rankings(
    self, horizon: str = "weekly", horizon_months: int = 1
) -> dict[str, Any]:
    runtime = get_runtime()
    now = datetime.now(timezone.utc)
    run_key = f"alpha:{horizon}:{horizon_months}:{now.date().isoformat()}"

    def generate() -> dict[str, Any]:
        result = runtime.alpha_rankings.generate(horizon, horizon_months)
        runtime.events.publish(
            "signals", f"{horizon}_alpha.updated", result
        )
        return {
            "run_key": run_key,
            "horizon": horizon,
            "horizon_months": horizon_months,
            "predictions_count": result["predictions_count"],
            "status": "complete",
        }

    return _execute_task(self, "alpha-rankings", run_key, generate)


@app.task(bind=True, name="workers.generate_growth_radar")
def generate_growth_radar(self) -> dict[str, Any]:
    runtime = get_runtime()
    now = datetime.now(timezone.utc)
    run_key = f"growth-radar:{now.date().isoformat()}"

    def generate() -> dict[str, Any]:
        result = runtime.growth_radar.generate()
        runtime.events.publish("signals", "growth_radar.updated", result)
        return {
            "run_key": run_key,
            "eligible_stocks": result["eligible_stocks"],
            "status": "complete",
        }

    return _execute_task(self, "growth-radar", run_key, generate)


@app.task(bind=True, name="workers.ingest_growth_features")
def ingest_growth_features(
    self,
    symbol: str,
    features: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    runtime = get_runtime()
    symbol = normalize_symbol(symbol)
    known_at = metadata["known_at"]
    digest = hashlib.sha256(
        json.dumps(
            {"features": features, "evidence": metadata.get("evidence", [])},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    run_key = f"growth-features:{symbol}:{known_at}:{digest}"

    def ingest() -> dict[str, Any]:
        saver = getattr(runtime.snapshots, "save_growth_features", None)
        if saver is None:
            raise RuntimeError("Snapshot repository does not support growth features")
        created = saver(
            {
                "symbol": symbol,
                "as_of": metadata["as_of"],
                "known_at": known_at,
                "source_version": metadata.get("source_version", FEATURE_SET_VERSION),
                "freshness_status": metadata.get("freshness_status", "CURRENT"),
                "features": features,
                "evidence": metadata.get("evidence", []),
            }
        )
        return {"symbol": symbol, "created": created, "status": "complete"}

    return _execute_task(self, "growth-features", run_key, ingest)


@app.task(bind=True, name="workers.ingest_filing_document")
def ingest_filing_document(
    self,
    symbol: str,
    document: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    runtime = get_runtime()
    symbol = normalize_symbol(symbol)
    payload_hash = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_key = f"filing:{symbol}:{metadata['source']}:{payload_hash}"

    def ingest() -> dict[str, Any]:
        saver = getattr(runtime.snapshots, "save_filing_document", None)
        if saver is None:
            raise RuntimeError("Snapshot repository does not support filing documents")
        status = metadata.get("extraction_status", "COMPLETE")
        if status not in {"COMPLETE", "PARTIAL", "MANUAL_REVIEW", "FAILED"}:
            raise ValueError("Invalid extraction_status")
        created = saver(
            {
                "symbol": symbol,
                "document_type": metadata["document_type"],
                "source": metadata["source"],
                "source_url": metadata["source_url"],
                "published_at": metadata["published_at"],
                "known_at": metadata.get("known_at", metadata["published_at"]),
                "payload_hash": payload_hash,
                "extraction_status": status,
                "raw_payload": document,
                "extracted_features": metadata.get("extracted_features", {}),
            }
        )
        return {
            "symbol": symbol,
            "created": created,
            "payload_hash": payload_hash,
            "extraction_status": status,
        }

    return _execute_task(self, "filing-document", run_key, ingest)


@app.task(bind=True, name="workers.ingest_fundamental_features")
def ingest_fundamental_features(
    self,
    symbol: str,
    metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    runtime = get_runtime()
    symbol = normalize_symbol(symbol)
    payload_hash = hashlib.sha256(
        json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_key = (
        f"fundamental:{symbol}:{metadata['fiscal_period_end']}:"
        f"{metadata.get('source_version', '1')}:{payload_hash}"
    )

    def ingest() -> dict[str, Any]:
        scored = FundamentalService().score(
            {**metrics, "known_at": metadata.get("known_at")}
        )
        saver = getattr(runtime.snapshots, "save_fundamental_features", None)
        if saver is None:
            raise RuntimeError("Snapshot repository does not support fundamental features")
        created = saver(
            {
                "symbol": symbol,
                "fiscal_period_end": metadata["fiscal_period_end"],
                "period_type": metadata.get("period_type", "TTM"),
                "source": metadata["source"],
                "source_version": metadata.get("source_version", "1"),
                "published_at": metadata["published_at"],
                "known_at": metadata.get("known_at", metadata["published_at"]),
                "payload_hash": payload_hash,
                "raw_payload": metrics,
                **scored,
            }
        )
        return {"symbol": symbol, "created": created, "score": scored["score"]}

    return _execute_task(self, "fundamental-features", run_key, ingest)


@app.task(bind=True, name="workers.aggregate_sentiment_features")
def aggregate_sentiment_features(
    self,
    symbol: str,
    items: list[dict[str, Any]],
    model_name: str = "finbert",
    model_version: str = "baseline",
) -> dict[str, Any]:
    runtime = get_runtime()
    symbol = normalize_symbol(symbol)
    as_of = datetime.now(timezone.utc)
    run_key = f"sentiment:{symbol}:{model_version}:{int(as_of.timestamp()) // 900}"

    def aggregate() -> dict[str, Any]:
        scored = SentimentService().aggregate(items, as_of=as_of)
        saver = getattr(runtime.snapshots, "save_sentiment_features", None)
        if saver is None:
            raise RuntimeError("Snapshot repository does not support sentiment features")
        created = saver(
            {
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "model_name": model_name,
                "model_version": model_version,
                **scored,
            }
        )
        return {"symbol": symbol, "created": created, "score": scored["score"]}

    return _execute_task(self, "sentiment-features", run_key, aggregate)


@app.task(bind=True, name="workers.ingest_legal_risk")
def ingest_legal_risk(
    self,
    symbol: str,
    events: list[dict[str, Any]],
    source_version: str = "legal-v1",
) -> dict[str, Any]:
    runtime = get_runtime()
    symbol = normalize_symbol(symbol)
    as_of = datetime.now(timezone.utc)
    digest = hashlib.sha256(
        json.dumps(events, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_key = f"legal:{symbol}:{source_version}:{digest}"

    def ingest() -> dict[str, Any]:
        scored = LegalRiskService().score(events, as_of=as_of)
        saver = getattr(runtime.snapshots, "save_legal_risk", None)
        if saver is None:
            raise RuntimeError("Snapshot repository does not support legal risk")
        created = saver(
            {
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "source_version": source_version,
                **scored,
            }
        )
        return {
            "symbol": symbol,
            "created": created,
            "risk_quotient": scored["risk_quotient"],
        }

    return _execute_task(self, "legal-risk", run_key, ingest)
