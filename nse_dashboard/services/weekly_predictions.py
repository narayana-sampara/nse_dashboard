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
from nse_dashboard.trading.chartink import chartink_macd_trend_signal
from sector_map import SECTOR_MAP, display_name


class ExplainableWeeklyModel:
    """Versioned, deterministic inference model for five-session candidates.

    This is the production baseline for the future trained model.  It combines
    non-linear technical and liquidity features, exposes its reasons, and keeps
    the inference contract stable so a validated ML artifact can replace it.
    """

    name = "explainable_weekly_ranker"
    version = "1.0.0"
    minimum_rows = 210

    def predict(self, symbol: str, sector: str, frame: pd.DataFrame) -> dict[str, Any]:
        frame = frame.dropna(subset=["Close", "Volume"]).copy()
        if len(frame) < self.minimum_rows:
            raise ValueError(f"Weekly model needs {self.minimum_rows} sessions")

        close = frame["Close"].astype(float)
        volume = frame["Volume"].astype(float)
        price = float(close.iloc[-1])
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        returns = close.pct_change()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - (100 / (1 + gain / loss.where(loss != 0, 1e-12)))

        momentum_5d = float(close.pct_change(5).iloc[-1] * 100)
        momentum_20d = float(close.pct_change(20).iloc[-1] * 100)
        volatility_20d = float(returns.rolling(20).std().iloc[-1] * math.sqrt(5) * 100)
        volume_ratio = float(volume.iloc[-1] / max(volume.tail(20).mean(), 1))
        average_traded_value = float((close * volume).tail(20).mean())
        latest_rsi = float(rsi.iloc[-1])

        trend_20_50 = (float(ema20.iloc[-1]) / max(float(ema50.iloc[-1]), 1e-9) - 1) * 100
        trend_50_200 = (float(ema50.iloc[-1]) / max(float(ema200.iloc[-1]), 1e-9) - 1) * 100
        score = 50.0
        score += max(-15, min(15, momentum_5d * 2.0))
        score += max(-12, min(12, momentum_20d * 0.6))
        score += max(-8, min(8, trend_20_50 * 2.0))
        score += max(-6, min(6, trend_50_200))
        score += max(-5, min(5, (volume_ratio - 1) * 5))
        if latest_rsi > 75:
            score -= 8
        elif 52 <= latest_rsi <= 68:
            score += 6
        elif latest_rsi < 35:
            score -= 8
        score -= max(0, volatility_20d - 6) * 1.2
        score = max(0.0, min(100.0, score))

        probability = 1 / (1 + math.exp(-(score - 55) / 11))
        predicted_return = (
            momentum_5d * 0.30
            + momentum_20d * 0.12
            + trend_20_50 * 0.25
            + max(0, volume_ratio - 1) * 0.55
            - max(0, volatility_20d - 5) * 0.12
        )
        predicted_return = max(-12.0, min(12.0, predicted_return))
        risk_score = max(0.0, min(100.0, volatility_20d * 7 + max(0, latest_rsi - 70)))

        reasons = []
        if momentum_5d > 1:
            reasons.append("positive five-day momentum")
        if momentum_20d > 3:
            reasons.append("positive 20-day momentum")
        if trend_20_50 > 0:
            reasons.append("20 EMA above 50 EMA")
        if trend_50_200 > 0:
            reasons.append("50 EMA above 200 EMA")
        if volume_ratio >= 1.2:
            reasons.append("volume above 20-day average")
        if 52 <= latest_rsi <= 68:
            reasons.append("RSI confirms controlled strength")
        if not reasons:
            reasons.append("mixed technical setup")

        as_of = close.index[-1]
        if hasattr(as_of, "date"):
            as_of = as_of.date().isoformat()
        else:
            as_of = str(as_of)
        return {
            "symbol": symbol,
            "name": display_name(symbol),
            "sector": sector,
            "price": round(price, 2),
            "as_of": as_of,
            "predicted_5d_return_pct": round(predicted_return, 2),
            "target_probability": round(probability, 4),
            "ranking_score": round(score, 2),
            "risk_score": round(risk_score, 2),
            "average_traded_value": round(average_traded_value, 2),
            "features": {
                "momentum_5d": round(momentum_5d, 2),
                "momentum_20d": round(momentum_20d, 2),
                "rsi_14": round(latest_rsi, 2),
                "volume_ratio": round(volume_ratio, 2),
                "volatility_5d": round(volatility_20d, 2),
                "ema_20_50_spread": round(trend_20_50, 2),
                "ema_50_200_spread": round(trend_50_200, 2),
            },
            "reasons": reasons[:4],
        }


class WeeklyPredictionService:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        snapshots: SnapshotRepository,
        period: str = "10y",
        model: ExplainableWeeklyModel | None = None,
        external_market_context: ExternalMarketContextProvider | None = None,
    ) -> None:
        self.adapter = adapter
        self.snapshots = snapshots
        self.period = period
        self.model = model or ExplainableWeeklyModel()
        self.external_market_context = external_market_context

    def generate(
        self,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        min_probability: float = 0.60,
        min_expected_return: float = 2.0,
        min_average_traded_value: float = 10_000_000,
        limit_per_sector: int = 5,
    ) -> dict[str, Any]:
        if min_price is not None and min_price < 0:
            raise ValueError("min_price must be zero or greater")
        if max_price is not None and max_price <= 0:
            raise ValueError("max_price must be greater than zero")
        if min_price is not None and max_price is not None and max_price <= min_price:
            raise ValueError("max_price must be greater than min_price")
        if not 0 <= min_probability <= 1:
            raise ValueError("min_probability must be between zero and one")
        if not 1 <= limit_per_sector <= 20:
            raise ValueError("limit_per_sector must be between 1 and 20")

        indian_context = (
            self.external_market_context.daily_market_context()
            if self.external_market_context
            else None
        )
        histories = self.adapter.market_history(list(SECTOR_MAP), self.period)
        monthly_snapshot = self.snapshots.latest_monthly_predictions(1, None, 20)
        approved = {
            item["symbol"]
            for group in monthly_snapshot.get("sectors", [])
            for item in group.get("picks", [])
        }
        monthly_gate_active = bool(monthly_snapshot.get("predictions_count"))
        monthly_regime = monthly_snapshot.get("regime", {}).get("state", "UNAVAILABLE")
        ranked: dict[str, list[dict[str, Any]]] = defaultdict(list)
        indicator_ranked: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: {"BUY": [], "SELL": []}
        )
        failures: list[str] = []
        eligible_count = 0
        analyzed_dates: list[str] = []
        for symbol, sector in SECTOR_MAP.items():
            try:
                candidate = self.model.predict(symbol, sector, histories[symbol])
                annotate_candidate_with_context(candidate, indian_context)
                try:
                    candidate["indicator"] = chartink_macd_trend_signal(
                        histories[symbol], "weekly"
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    candidate["indicator"] = {
                        "timeframe": "weekly", "signal": "HOLD", "strength_score": 0.0,
                        "rejection_reasons": [str(exc)], "features": {},
                    }
                candidate["monthly_approved"] = symbol in approved
                candidate["monthly_regime"] = monthly_regime
                candidate["entry_allowed"] = (
                    (not monthly_gate_active or symbol in approved)
                    and monthly_regime != "RISK_OFF"
                )
                analyzed_dates.append(candidate["as_of"])
                if min_price is not None and candidate["price"] < min_price:
                    continue
                if max_price is not None and candidate["price"] > max_price:
                    continue
                indicator_signal = str(candidate["indicator"]["signal"])
                if indicator_signal in ("BUY", "SELL"):
                    indicator_ranked[sector][indicator_signal].append(candidate)
                if candidate["average_traded_value"] < min_average_traded_value:
                    continue
                eligible_count += 1
                if candidate["target_probability"] < min_probability:
                    continue
                if candidate["predicted_5d_return_pct"] < min_expected_return:
                    continue
                if not candidate["entry_allowed"]:
                    continue
                ranked[sector].append(candidate)
            except (KeyError, TypeError, ValueError):
                failures.append(symbol)

        sectors = []
        for sector in sorted(set(SECTOR_MAP.values())):
            picks = sorted(
                ranked.get(sector, []),
                key=lambda item: (
                    -float(item["ranking_score"]),
                    -float(item["target_probability"]),
                    str(item["symbol"]),
                ),
            )[:limit_per_sector]
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
        valid_until = (pd.Timestamp(now.date()) + pd.offsets.BDay(5)).date().isoformat()
        result = {
            "generated_at": now.isoformat(),
            "market_date": max(analyzed_dates) if analyzed_dates else None,
            "valid_until": valid_until,
            "source": self.adapter.name,
            "external_market_context": summarize_external_context(
                indian_context, self.adapter.name
            ),
            "model": {"name": self.model.name, "version": self.model.version},
            "indicator_model": {
                "name": "chartink_macd_ema_adx_crossover", "version": "1.0.0",
                "timeframe": "weekly", "sell_rule": "exact_bearish_inverse",
            },
            "filters": {
                "min_price": min_price,
                "max_price": max_price,
                "min_probability": min_probability,
                "min_expected_return": min_expected_return,
                "min_average_traded_value": min_average_traded_value,
                "limit_per_sector": limit_per_sector,
            },
            "monthly_gate_active": monthly_gate_active,
            "monthly_regime": monthly_regime,
            "universe_size": len(SECTOR_MAP),
            "eligible_stocks": eligible_count,
            "predictions_count": sum(len(item["picks"]) for item in sectors),
            "buy_count": sum(len(item["buys"]) for item in sectors),
            "sell_count": sum(len(item["sells"]) for item in sectors),
            "failures": failures,
            "sectors": sectors,
            "disclaimer": "Model estimates are research signals, not guaranteed returns or investment advice.",
        }
        result = json_ready(result)
        self.snapshots.save_weekly_predictions(result)
        return result

    def latest(self, max_price: float | None = None, limit_per_sector: int = 5) -> dict[str, Any]:
        return self.snapshots.latest_weekly_predictions(max_price, limit_per_sector)

    def history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.snapshots.weekly_prediction_history(symbol.strip().upper(), limit)
