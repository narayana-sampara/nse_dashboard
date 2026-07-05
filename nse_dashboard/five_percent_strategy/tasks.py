from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from nse_dashboard.infrastructure.idempotency import TaskAlreadyRunning, execute_once
from nse_dashboard.workers.celery_app import app
from nse_dashboard.workers.runtime import get_runtime


def _execute_task(task, stage: str, run_key: str, operation):
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
    except Exception:
        raise task.retry(countdown=30, max_retries=3)


@app.task(bind=True, name="five_percent_strategy.run_daily_scan")
def run_daily_scan(
    self,
    target_pct: float | None = None,
    stop_loss_pct: float | None = None,
    holding_days: int | None = None,
    probability_threshold: float | None = None,
    max_candidates: int | None = None,
    initial_capital: float | None = None,
    min_avg_volume: float | None = None,
    min_avg_turnover: float | None = None,
) -> dict[str, Any]:
    runtime = get_runtime()
    settings = runtime.settings
    parameters = {
        "target_pct": target_pct if target_pct is not None else settings.five_percent_target_pct,
        "stop_loss_pct": stop_loss_pct if stop_loss_pct is not None else settings.five_percent_stop_loss_pct,
        "holding_days": holding_days if holding_days is not None else settings.five_percent_holding_days,
        "probability_threshold": (
            probability_threshold
            if probability_threshold is not None
            else settings.five_percent_probability_threshold
        ),
        "max_candidates": max_candidates if max_candidates is not None else settings.five_percent_max_candidates,
        "initial_capital": initial_capital if initial_capital is not None else 10_000.0,
        "min_avg_volume": min_avg_volume if min_avg_volume is not None else settings.five_percent_min_avg_volume,
        "min_avg_turnover": (
            min_avg_turnover if min_avg_turnover is not None else settings.five_percent_min_avg_turnover
        ),
    }
    run_key = f"scan:{date.today().isoformat()}"

    def generate() -> dict[str, Any]:
        result = runtime.five_percent_strategy.generate(**parameters)
        runtime.events.publish("signals", "five_percent_strategy.scan_completed", result)
        top_candidate = next(iter(result.get("candidates", [])), None)
        if top_candidate and top_candidate.get("probability_score", 0) >= 80:
            runtime.events.publish(
                "signals", "five_percent_strategy.candidate_generated", top_candidate
            )
        return {
            "run_id": result["run_id"],
            "candidates_count": result["candidates_count"],
            "status": "complete",
        }

    return _execute_task(self, "five-percent-scan", run_key, generate)


@app.task(bind=True, name="five_percent_strategy.run_backtest")
def run_backtest(
    self,
    start_date: str,
    end_date: str,
    initial_capital: float = 10_000.0,
    target_pct: float = 5.0,
    stop_loss_pct: float = 2.0,
    holding_days: int = 5,
    probability_threshold: float = 65.0,
    max_trades: int = 200,
    cost_assumption_bps: float = 30.0,
    slippage_bps: float = 10.0,
) -> dict[str, Any]:
    runtime = get_runtime()
    run_key = f"backtest:{start_date}:{end_date}:{target_pct}:{stop_loss_pct}:{holding_days}"

    def generate() -> dict[str, Any]:
        result = runtime.five_percent_strategy.backtest(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            target_pct=target_pct,
            stop_loss_pct=stop_loss_pct,
            holding_days=holding_days,
            probability_threshold=probability_threshold,
            max_trades=max_trades,
            cost_assumption_bps=cost_assumption_bps,
            slippage_bps=slippage_bps,
        )
        runtime.events.publish("signals", "five_percent_strategy.backtest_completed", result)
        return {"backtest_id": result["backtest_id"], "status": "complete"}

    return _execute_task(self, "five-percent-backtest", run_key, generate)


@app.task(bind=True, name="five_percent_strategy.update_paper_trades")
def update_paper_trades(self) -> dict[str, Any]:
    runtime = get_runtime()
    run_key = f"paper-trades:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M')}"

    def update() -> dict[str, Any]:
        events = runtime.five_percent_strategy.update_paper_trades_mark_to_market()
        for event in events:
            runtime.events.publish("signals", event["event"], event["trade"])
        return {"updated": len(events), "status": "complete"}

    return _execute_task(self, "five-percent-paper-trades", run_key, update)


@app.task(bind=True, name="five_percent_strategy.publish_latest_signals")
def publish_latest_signals(self) -> dict[str, Any]:
    runtime = get_runtime()
    latest = runtime.five_percent_strategy.latest()
    runtime.events.publish("signals", "five_percent_strategy.scan_completed", latest)
    return {"run_id": latest.get("run_id"), "status": "published"}
