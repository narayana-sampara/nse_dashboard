from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from nse_dashboard.core.json import json_ready
from nse_dashboard.domain.market_data import MarketDataAdapter
from nse_dashboard.domain.snapshots import SnapshotRepository
from nse_dashboard.services.external_market_context import (
    ExternalMarketContextProvider,
    annotate_candidate_with_context,
    summarize_external_context,
)
from sector_map import SECTOR_MAP, display_name
from nse_dashboard.trading.monthly import ConservativeMonthlyStrategy
from nse_dashboard.trading.chartink import chartink_macd_trend_signal


class ExplainableMonthlyModel:
    """Horizon-aware, explainable baseline scored from zero to 100."""

    name = "explainable_monthly_ranker"
    version = "1.0.0"
    minimum_rows = 300

    def predict(
        self, symbol: str, sector: str, frame: pd.DataFrame, horizon_months: int
    ) -> dict[str, Any]:
        if not 1 <= horizon_months <= 12:
            raise ValueError("horizon_months must be between 1 and 12")
        frame = frame.dropna(subset=["Close", "Volume"]).copy()
        if len(frame) < self.minimum_rows:
            raise ValueError(f"Monthly model needs {self.minimum_rows} sessions")
        frame.index = pd.to_datetime(frame.index)
        daily_traded_value = float(
            (frame["Close"].astype(float) * frame["Volume"].astype(float)).tail(20).mean()
        )
        monthly = frame.resample("ME").agg({"Close": "last", "Volume": "sum"}).dropna()
        latest_session = frame.index.max().normalize()
        expected_month_end = pd.offsets.BMonthEnd().rollforward(latest_session).normalize()
        if latest_session < expected_month_end:
            monthly = monthly.iloc[:-1]
        if len(monthly) < 18:
            raise ValueError("Monthly model needs at least 18 completed monthly bars")

        close = monthly["Close"].astype(float)
        volume = monthly["Volume"].astype(float)
        returns = close.pct_change()
        price = float(close.iloc[-1])
        ema3 = close.ewm(span=3, adjust=False).mean()
        ema6 = close.ewm(span=6, adjust=False).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - (100 / (1 + gain / loss.where(loss != 0, 1e-12)))

        horizon_bars = min(horizon_months, len(close) - 1)
        momentum_1m = float(close.pct_change(1).iloc[-1] * 100)
        horizon_momentum = float(close.pct_change(horizon_bars).iloc[-1] * 100)
        annualized_volatility = float(returns.tail(12).std() * math.sqrt(12) * 100)
        volume_ratio = float(volume.tail(3).mean() / max(volume.tail(12).mean(), 1))
        average_traded_value = daily_traded_value
        latest_rsi = float(rsi.iloc[-1])
        rolling_high = float(close.tail(12).max())
        drawdown = (price / max(rolling_high, 1e-9) - 1) * 100

        trend_score = 0.0
        trend_score += 10 if price > float(ema12.iloc[-1]) else 0
        trend_score += 10 if float(ema6.iloc[-1]) > float(ema12.iloc[-1]) else 0
        trend_score += 10 if float(ema3.iloc[-1]) > float(ema6.iloc[-1]) else 0

        momentum_score = max(0.0, min(10.0, 5 + momentum_1m * 0.7))
        normalized_horizon = horizon_momentum / math.sqrt(horizon_months)
        momentum_score += max(0.0, min(20.0, 10 + normalized_horizon * 0.65))

        volume_score = max(0.0, min(10.0, 5 + (volume_ratio - 1) * 12))
        if 50 <= latest_rsi <= 68:
            rsi_score = 10.0
        elif 40 <= latest_rsi < 50 or 68 < latest_rsi <= 75:
            rsi_score = 6.0
        elif 30 <= latest_rsi < 40:
            rsi_score = 3.0
        else:
            rsi_score = 0.0

        volatility_points = max(0.0, min(10.0, 10 - max(0, annualized_volatility - 20) * 0.25))
        drawdown_points = max(0.0, min(10.0, 10 + drawdown * 0.5))
        risk_score_component = volatility_points + drawdown_points
        total_score = max(
            0.0,
            min(100.0, trend_score + momentum_score + volume_score + rsi_score + risk_score_component),
        )

        probability = 1 / (1 + math.exp(-(total_score - 58) / 11))
        predicted_return = (
            horizon_momentum * 0.35
            + momentum_1m * math.sqrt(horizon_months) * 0.25
            + max(0, trend_score - 15) * 0.10 * math.sqrt(horizon_months)
            - max(0, annualized_volatility - 30) * 0.05 * math.sqrt(horizon_months)
        )
        predicted_return = max(-40.0, min(60.0, predicted_return))
        risk_score = max(
            0.0,
            min(100.0, annualized_volatility * 1.5 + abs(min(0, drawdown)) * 0.8),
        )

        reasons = []
        if trend_score >= 20:
            reasons.append("positive medium- and long-term trend")
        if horizon_momentum > 5:
            reasons.append(f"positive {horizon_months}-month momentum")
        if momentum_1m > 2:
            reasons.append("recent momentum is strengthening")
        if volume_ratio >= 1.1:
            reasons.append("three-month volume is above its 12-month baseline")
        if 50 <= latest_rsi <= 68:
            reasons.append("RSI shows controlled strength")
        if risk_score_component >= 14:
            reasons.append("volatility and drawdown remain controlled")
        if not reasons:
            reasons.append("mixed monthly setup")

        as_of = close.index[-1]
        as_of = as_of.date().isoformat() if hasattr(as_of, "date") else str(as_of)
        return {
            "symbol": symbol,
            "name": display_name(symbol),
            "sector": sector,
            "price": round(price, 2),
            "as_of": as_of,
            "horizon_months": horizon_months,
            "predicted_return_pct": round(predicted_return, 2),
            "target_probability": round(probability, 4),
            "score": round(total_score, 2),
            "risk_score": round(risk_score, 2),
            "average_traded_value": round(average_traded_value, 2),
            "score_breakdown": {
                "trend": round(trend_score, 2),
                "momentum": round(momentum_score, 2),
                "volume": round(volume_score, 2),
                "rsi_quality": round(rsi_score, 2),
                "risk_control": round(risk_score_component, 2),
            },
            "score_maximums": {
                "trend": 30,
                "momentum": 30,
                "volume": 10,
                "rsi_quality": 10,
                "risk_control": 20,
            },
            "features": {
                "momentum_1m": round(momentum_1m, 2),
                "horizon_momentum": round(horizon_momentum, 2),
                "rsi_14": round(latest_rsi, 2),
                "annualized_volatility": round(annualized_volatility, 2),
                "drawdown_from_52w_high": round(drawdown, 2),
                "volume_ratio": round(volume_ratio, 2),
                "monthly_bars": len(monthly),
            },
            "reasons": reasons[:4],
        }


class MonthlyPredictionService:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        snapshots: SnapshotRepository,
        period: str = "max",
        model: ExplainableMonthlyModel | None = None,
        strategy: ConservativeMonthlyStrategy | None = None,
        external_market_context: ExternalMarketContextProvider | None = None,
    ) -> None:
        self.adapter = adapter
        self.snapshots = snapshots
        self.period = period
        self.model = model or ExplainableMonthlyModel()
        self.strategy = strategy or ConservativeMonthlyStrategy()
        self.external_market_context = external_market_context

    def generate(
        self,
        horizon_months: int,
        *,
        max_price: float | None = None,
        min_score: float = 60,
        min_average_traded_value: float = 10_000_000,
        limit_per_sector: int = 5,
    ) -> dict[str, Any]:
        if not 1 <= horizon_months <= 12:
            raise ValueError("horizon_months must be between 1 and 12")
        if max_price is not None and max_price <= 0:
            raise ValueError("max_price must be greater than zero")
        if not 0 <= min_score <= 100:
            raise ValueError("min_score must be between zero and 100")
        if not 1 <= limit_per_sector <= 20:
            raise ValueError("limit_per_sector must be between 1 and 20")

        indian_context = (
            self.external_market_context.daily_market_context()
            if self.external_market_context
            else None
        )
        benchmark_symbol = "^CNX100"
        histories = self.adapter.market_history([*SECTOR_MAP, benchmark_symbol], self.period)
        benchmark = histories[benchmark_symbol]
        regime = self.strategy.regime(benchmark)
        ranked: dict[str, list[dict[str, Any]]] = defaultdict(list)
        indicator_ranked: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: {"BUY": [], "SELL": []}
        )
        analyzed_dates: list[str] = []
        failures: list[str] = []
        eligible_count = 0
        for symbol, sector in SECTOR_MAP.items():
            try:
                candidate = self.strategy.evaluate(
                    symbol, sector, histories[symbol], benchmark
                )
                annotate_candidate_with_context(candidate, indian_context)
                momentum = float(candidate["features"]["momentum_6m"])
                candidate.update(
                    {
                        "horizon_months": horizon_months,
                        "predicted_return_pct": round(
                            max(-40.0, min(60.0, momentum / 6 * horizon_months)), 2
                        ),
                        "target_probability": round(
                            1 / (1 + math.exp(-(float(candidate["score"]) - 58) / 11)), 4
                        ),
                        "risk_score": round(
                            min(100.0, float(candidate["features"]["annualized_volatility"]) * 1.5), 2
                        ),
                    }
                )
                try:
                    candidate["indicator"] = chartink_macd_trend_signal(
                        histories[symbol], "monthly"
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    candidate["indicator"] = {
                        "timeframe": "monthly", "signal": "HOLD", "strength_score": 0.0,
                        "rejection_reasons": [str(exc)], "features": {},
                    }
                analyzed_dates.append(candidate["as_of"])
                if max_price is not None and candidate["price"] > max_price:
                    continue
                indicator_signal = str(candidate["indicator"]["signal"])
                if indicator_signal in ("BUY", "SELL"):
                    indicator_ranked[sector][indicator_signal].append(candidate)
                if candidate["average_traded_value"] < min_average_traded_value:
                    continue
                eligible_count += 1
                if candidate["score"] >= min_score:
                    ranked[sector].append(candidate)
            except (KeyError, TypeError, ValueError):
                failures.append(symbol)

        selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
        all_candidates = sorted(
            (item for values in ranked.values() for item in values),
            key=lambda item: (-float(item["score"]), -float(item["target_probability"]), str(item["symbol"])),
        )
        for item in all_candidates:
            sector = str(item["sector"])
            if len(selected[sector]) >= limit_per_sector:
                continue
            selected[sector].append(item)

        sectors = []
        for sector in sorted(set(SECTOR_MAP.values())):
            picks = selected.get(sector, [])
            for rank, item in enumerate(picks, start=1):
                item["sector_rank"] = rank
            signals: dict[str, list[dict[str, Any]]] = {}
            for signal, key in (("BUY", "buys"), ("SELL", "sells")):
                values = sorted(
                    indicator_ranked[sector][signal],
                    key=lambda item: (
                        -float(item["indicator"]["strength_score"]), str(item["symbol"])
                    ),
                )[:limit_per_sector]
                for signal_rank, item in enumerate(values, start=1):
                    item[f"{signal.lower()}_rank"] = signal_rank
                signals[key] = values
            sectors.append({"name": sector, "picks": picks, **signals})

        now = datetime.now(timezone.utc)
        result = {
            "generated_at": now.isoformat(),
            "market_date": max(analyzed_dates) if analyzed_dates else None,
            "horizon_months": horizon_months,
            "source": self.adapter.name,
            "external_market_context": summarize_external_context(
                indian_context, self.adapter.name
            ),
            "model": {"name": self.model.name, "version": "2.0.0"},
            "indicator_model": {
                "name": "chartink_macd_ema_adx_crossover", "version": "1.0.0",
                "timeframe": "monthly", "sell_rule": "exact_bearish_inverse",
            },
            "strategy": {"name": self.strategy.name, "version": self.strategy.version},
            "strategy_version": self.strategy.version,
            "regime": regime,
            "filters": {
                "max_price": max_price,
                "min_score": min_score,
                "min_average_traded_value": min_average_traded_value,
                "limit_per_sector": limit_per_sector,
            },
            # Retained for API compatibility with existing consumers.
            "score_method": {
                "trend": 30, "momentum": 30, "volume": 10,
                "rsi_quality": 10, "risk_control": 20,
            },
            "selection_method": {
                "relative_strength": 30, "momentum_12_1": 25,
                "momentum_6m": 20, "trend_strength": 15,
                "liquidity_volatility": 10,
            },
            "universe_size": len(SECTOR_MAP),
            "eligible_stocks": eligible_count,
            "predictions_count": sum(len(sector["picks"]) for sector in sectors),
            "buy_count": sum(len(sector["buys"]) for sector in sectors),
            "sell_count": sum(len(sector["sells"]) for sector in sectors),
            "failures": failures,
            "sectors": sectors,
            "disclaimer": "Scores are model estimates for research, not guaranteed returns or investment advice.",
        }
        result = json_ready(result)
        self.snapshots.save_monthly_predictions(result)
        return result

    def latest(
        self, horizon_months: int, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]:
        return self.snapshots.latest_monthly_predictions(
            horizon_months, max_price, limit_per_sector
        )

    def history(
        self, symbol: str, horizon_months: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.snapshots.monthly_prediction_history(
            symbol.strip().upper(), horizon_months, limit
        )
