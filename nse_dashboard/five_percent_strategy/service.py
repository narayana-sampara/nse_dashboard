from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nse_dashboard.core.json import json_ready
from nse_dashboard.domain.market_data import MarketDataAdapter
from nse_dashboard.five_percent_strategy.backtester import BacktestConfig, run_backtest
from nse_dashboard.five_percent_strategy.baseline_model import ExplainableBaselineModel
from nse_dashboard.five_percent_strategy.features import compute_features
from nse_dashboard.five_percent_strategy.models import Candidate
from nse_dashboard.five_percent_strategy.risk import RiskLimits, passes_liquidity_and_volatility_filters
from sector_map import SECTOR_MAP, display_name

DISCLAIMER = (
    "This module generates research-based trading signals using historical data, "
    "technical factors, and probability scoring. It does not guarantee 5% returns. "
    "Trading involves risk, including loss of capital. Backtested results may not "
    "match live performance due to slippage, costs, liquidity, and market conditions."
)

STRATEGY_VERSION = "1.0.0"


class FivePercentStrategyService:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        repository: Any,
        period: str = "3y",
        model: ExplainableBaselineModel | None = None,
    ) -> None:
        self.adapter = adapter
        self.repository = repository
        self.period = period
        self.model = model or ExplainableBaselineModel()

    def generate(
        self,
        *,
        target_pct: float = 5.0,
        stop_loss_pct: float = 2.0,
        holding_days: int = 5,
        probability_threshold: float = 65.0,
        max_candidates: int = 20,
        initial_capital: float = 10_000.0,
        min_avg_volume: float = 0.0,
        min_avg_turnover: float = 10_000_000.0,
    ) -> dict[str, Any]:
        model = ExplainableBaselineModel(target_pct=target_pct, stop_loss_pct=stop_loss_pct)
        limits = RiskLimits(min_avg_volume=min_avg_volume, min_avg_turnover=min_avg_turnover)

        histories = self.adapter.market_history(list(SECTOR_MAP), self.period)
        try:
            nifty_frame = self.adapter.history("^NSEI", self.period)
        except Exception:
            nifty_frame = None

        scored: list[Candidate] = []
        analyzed_dates: list[str] = []
        skipped: list[dict[str, str]] = []
        for symbol, sector in SECTOR_MAP.items():
            frame = histories.get(symbol)
            if frame is None or frame.empty:
                skipped.append({"symbol": symbol, "reason": "no market data available"})
                continue
            try:
                features = compute_features(
                    symbol, frame, nifty_frame=nifty_frame, sector=sector, company_name=display_name(symbol)
                )
            except ValueError as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})
                continue
            analyzed_dates.append(features.as_of)

            ok, reason = passes_liquidity_and_volatility_filters(features, limits)
            if not ok:
                skipped.append({"symbol": symbol, "reason": reason or "filtered"})
                continue

            prediction = model.predict_candidates([features])[0]
            if prediction.probability_score < probability_threshold:
                continue

            scored.append(
                Candidate(
                    symbol=symbol,
                    company_name=features.company_name,
                    sector=features.sector,
                    close_price=features.close,
                    entry_price=prediction.entry_price,
                    target_price=prediction.target_price,
                    stop_loss_price=prediction.stop_loss_price,
                    probability_score=prediction.probability_score,
                    ai_score=prediction.ai_score,
                    rank=0,
                    expected_return_pct=prediction.expected_return_pct,
                    risk_reward_ratio=prediction.risk_reward_ratio,
                    avg_volume=features.avg_volume_20d,
                    avg_turnover=features.avg_traded_value_20d,
                    volatility=features.volatility,
                    rsi=features.rsi_14,
                    momentum_5d=features.momentum_5d,
                    momentum_20d=features.momentum_20d,
                    volume_ratio=features.volume_ratio,
                    trend_score=prediction.component_scores["trend_score"],
                    relative_strength_score=prediction.component_scores["relative_strength_score"],
                    breakout_score=prediction.component_scores["breakout_score"],
                    risk_score=prediction.component_scores["risk_score"],
                    reasons=prediction.reasons,
                )
            )

        scored.sort(key=lambda item: (-item.probability_score, -item.ai_score, item.symbol))
        scored = scored[:max_candidates]
        for rank, candidate in enumerate(scored, start=1):
            candidate.rank = rank

        now = datetime.now(timezone.utc)
        run_id = f"5pct-{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
        result = {
            "run_id": run_id,
            "created_at": now.isoformat(),
            "market_date": max(analyzed_dates) if analyzed_dates else None,
            "strategy_version": STRATEGY_VERSION,
            "model_version": model.version,
            "target_pct": target_pct,
            "stop_loss_pct": stop_loss_pct,
            "holding_days": holding_days,
            "probability_threshold": probability_threshold,
            "initial_capital": initial_capital,
            "max_candidates": max_candidates,
            "status": "complete",
            "universe_size": len(SECTOR_MAP),
            "candidates_count": len(scored),
            "skipped": skipped,
            "candidates": [candidate.as_dict() for candidate in scored],
            "disclaimer": DISCLAIMER,
        }
        result = json_ready(result)
        self.repository.save_five_percent_strategy_run(result)
        return result

    def latest(self) -> dict[str, Any]:
        return self.repository.latest_five_percent_strategy_run()

    def run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.five_percent_strategy_run_by_id(run_id)

    def symbol_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.five_percent_strategy_symbol_history(symbol.strip().upper(), limit)

    def backtest(
        self,
        *,
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
        diversify: bool = False,
        max_concurrent_trades: int = 5,
    ) -> dict[str, Any]:
        config = BacktestConfig(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            target_pct=target_pct,
            stop_loss_pct=stop_loss_pct,
            holding_days=holding_days,
            probability_threshold=probability_threshold,
            max_trades=max_trades,
            cost_bps=cost_assumption_bps,
            slippage_bps=slippage_bps,
            diversify=diversify,
            max_concurrent_trades=max_concurrent_trades,
        )
        histories = self.adapter.market_history(list(SECTOR_MAP), self.period)
        summary = run_backtest(histories, config)

        now = datetime.now(timezone.utc)
        backtest_id = f"bt-{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
        result = {
            "backtest_id": backtest_id,
            "created_at": now.isoformat(),
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "target_pct": target_pct,
            "stop_loss_pct": stop_loss_pct,
            "holding_days": holding_days,
            "status": "complete",
            "assumptions": {
                "probability_threshold": probability_threshold,
                "max_trades": max_trades,
                "cost_assumption_bps": cost_assumption_bps,
                "slippage_bps": slippage_bps,
                "diversify": diversify,
                "max_concurrent_trades": max_concurrent_trades,
            },
            "disclaimer": DISCLAIMER,
            **summary,
        }
        result = json_ready(result)
        self.repository.save_five_percent_backtest_run(result)
        return result

    def project_compounding(
        self,
        *,
        initial_capital: float = 10_000.0,
        target_pct: float = 5.0,
        stop_loss_pct: float = 2.0,
        number_of_trades: int = 200,
        expected_win_rate: float = 70.0,
        cost_per_trade_pct: float = 0.3,
    ) -> dict[str, Any]:
        scenarios = {
            "perfect": 100.0,
            "win_rate_90": 90.0,
            "win_rate_80": 80.0,
            "win_rate_70": 70.0,
            "win_rate_60": 60.0,
            "custom": expected_win_rate,
        }
        results = {
            name: _project_capital(
                initial_capital, target_pct, stop_loss_pct, number_of_trades, win_rate, cost_per_trade_pct
            )
            for name, win_rate in scenarios.items()
        }
        return {
            "initial_capital": initial_capital,
            "target_pct": target_pct,
            "stop_loss_pct": stop_loss_pct,
            "number_of_trades": number_of_trades,
            "expected_win_rate": expected_win_rate,
            "cost_per_trade_pct": cost_per_trade_pct,
            "scenarios": results,
            "disclaimer": (
                "Perfect compounding is theoretical. Real trading includes losses, "
                "brokerage, taxes, slippage, liquidity issues, and gap risk."
            ),
        }

    def start_paper_trade(
        self,
        *,
        symbol: str,
        entry_price: float,
        target_price: float,
        stop_loss_price: float,
        capital_before: float,
        signal_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        trade = {
            "signal_id": signal_id,
            "symbol": symbol.strip().upper(),
            "entry_date": now.date().isoformat(),
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "current_price": entry_price,
            "status": "open",
            "capital_before": capital_before,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        return self.repository.save_five_percent_paper_trade(trade)

    def list_paper_trades(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_five_percent_paper_trades(status)

    def get_paper_trade(self, trade_id: int) -> dict[str, Any] | None:
        return self.repository.get_five_percent_paper_trade(trade_id)

    def close_paper_trade(
        self, trade_id: int, *, exit_price: float, exit_reason: str = "manual_close"
    ) -> dict[str, Any] | None:
        trade = self.repository.get_five_percent_paper_trade(trade_id)
        if trade is None:
            return None
        return_pct = (exit_price / trade["entry_price"] - 1) * 100
        capital_after = trade["capital_before"] * (1 + return_pct / 100)
        now = datetime.now(timezone.utc)
        update = {
            "status": "closed",
            "exit_date": now.date().isoformat(),
            "exit_price": exit_price,
            "return_pct": round(return_pct, 3),
            "capital_after": round(capital_after, 2),
            "exit_reason": exit_reason,
            "updated_at": now.isoformat(),
        }
        return self.repository.update_five_percent_paper_trade(trade_id, update)

    def update_paper_trades_mark_to_market(self) -> list[dict[str, Any]]:
        """Refresh open paper trades against the latest close and auto-close on target/stop hits."""

        open_trades = self.repository.list_five_percent_paper_trades("open")
        events: list[dict[str, Any]] = []
        symbols = sorted({trade["symbol"] for trade in open_trades})
        if not symbols:
            return events
        histories = self.adapter.market_history(symbols, "5d")
        for trade in open_trades:
            frame = histories.get(trade["symbol"])
            if frame is None or frame.empty:
                continue
            current_price = float(frame["Close"].iloc[-1])
            if current_price >= trade["target_price"]:
                updated = self.close_paper_trade(trade["id"], exit_price=current_price, exit_reason="target_hit")
                events.append({"event": "paper_trade_target_hit", "trade": updated})
            elif current_price <= trade["stop_loss_price"]:
                updated = self.close_paper_trade(trade["id"], exit_price=current_price, exit_reason="stop_hit")
                events.append({"event": "paper_trade_stop_hit", "trade": updated})
            else:
                self.repository.update_five_percent_paper_trade(
                    trade["id"],
                    {"current_price": current_price, "updated_at": datetime.now(timezone.utc).isoformat()},
                )
        return events


def _project_capital(
    initial_capital: float,
    target_pct: float,
    stop_loss_pct: float,
    number_of_trades: int,
    win_rate_pct: float,
    cost_per_trade_pct: float,
) -> dict[str, Any]:
    win_rate = win_rate_pct / 100
    win_multiplier = 1 + (target_pct - cost_per_trade_pct) / 100
    loss_multiplier = 1 - (stop_loss_pct + cost_per_trade_pct) / 100
    expected_multiplier = win_rate * win_multiplier + (1 - win_rate) * loss_multiplier
    final_capital = initial_capital * (expected_multiplier ** number_of_trades)
    total_return_pct = (final_capital / initial_capital - 1) * 100 if initial_capital else 0.0
    return {
        "win_rate_pct": win_rate_pct,
        "final_capital": round(final_capital, 2) if math.isfinite(final_capital) else None,
        "total_return_pct": round(total_return_pct, 2) if math.isfinite(total_return_pct) else None,
    }
