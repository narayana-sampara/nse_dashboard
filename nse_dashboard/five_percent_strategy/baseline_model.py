from __future__ import annotations

import math
from typing import Protocol

from nse_dashboard.five_percent_strategy.models import FivePercentPrediction, StockFeatureRow

COMPONENT_WEIGHTS = {
    "momentum_score": 0.25,
    "trend_score": 0.20,
    "volume_score": 0.15,
    "breakout_score": 0.15,
    "relative_strength_score": 0.15,
    "risk_score": 0.10,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class FivePercentPredictionModel(Protocol):
    """Replaceable inference contract. A trained ML model can implement this
    without changing the API/service layer."""

    name: str
    version: str

    def predict_candidates(
        self, features: list[StockFeatureRow]
    ) -> list[FivePercentPrediction]: ...


class ExplainableBaselineModel:
    """Deterministic, weighted scoring baseline for the 5% growth strategy.

    Produces an explainable probability/AI score and plain-English reasons so
    a trained model (logistic regression / random forest / gradient boosting)
    can later be swapped in behind the same ``predict_candidates`` contract.
    """

    name = "five_percent_explainable_baseline"
    version = "1.0.0"

    def __init__(self, *, target_pct: float = 5.0, stop_loss_pct: float = 2.0) -> None:
        self.target_pct = target_pct
        self.stop_loss_pct = stop_loss_pct

    def predict_candidates(
        self, features: list[StockFeatureRow]
    ) -> list[FivePercentPrediction]:
        return [self._predict_one(row) for row in features]

    def _predict_one(self, row: StockFeatureRow) -> FivePercentPrediction:
        momentum_score = _clamp(50 + row.momentum_5d * 4 + row.momentum_20d * 1.2)

        trend_bias = 0.0
        if row.close > row.ema_20:
            trend_bias += 12
        if row.close > row.ema_50:
            trend_bias += 8
        if row.ema_9 > row.ema_20:
            trend_bias += 10
        if row.ema_20 > row.ema_50:
            trend_bias += 10
        trend_score = _clamp(50 + trend_bias - 15)

        volume_score = _clamp(50 + (row.volume_ratio - 1) * 40)

        breakout_bias = 20 if row.breakout_20d_high else 0
        breakout_bias += _clamp(row.distance_from_52w_high_pct + 10, 0, 20) - 10
        breakout_score = _clamp(50 + breakout_bias)

        relative_strength_score = _clamp(50 + row.relative_strength_vs_nifty * 5)

        rsi_penalty = 0.0
        if row.rsi_14 > 78:
            rsi_penalty += 15
        elif row.rsi_14 < 30:
            rsi_penalty += 10
        volatility_penalty = max(0.0, row.volatility - 35) * 0.6
        risk_score = _clamp(100 - rsi_penalty - volatility_penalty)

        components = {
            "momentum_score": momentum_score,
            "trend_score": trend_score,
            "volume_score": volume_score,
            "breakout_score": breakout_score,
            "relative_strength_score": relative_strength_score,
            "risk_score": risk_score,
        }
        weighted = sum(components[key] * weight for key, weight in COMPONENT_WEIGHTS.items())
        probability_score = _clamp(weighted)
        ai_score = round(probability_score / 10, 1)

        entry_price = row.close
        target_price = entry_price * (1 + self.target_pct / 100)
        stop_loss_price = entry_price * (1 - self.stop_loss_pct / 100)
        risk_reward_ratio = self.target_pct / max(self.stop_loss_pct, 1e-9)

        reasons: list[str] = []
        if row.momentum_5d > 1:
            reasons.append("Strong 5-day momentum")
        if row.close > row.ema_20 and row.close > row.ema_50:
            reasons.append("Price is above EMA 20 and EMA 50")
        if row.volume_ratio >= 1.2:
            reasons.append(f"Volume is {row.volume_ratio:.1f}x higher than 20-day average")
        if row.relative_strength_vs_nifty > 0:
            reasons.append("Stock is outperforming Nifty")
        if row.atr_14 > 0 and (row.atr_14 / max(entry_price, 1e-9)) * 100 * math.sqrt(5) >= self.target_pct * 0.6:
            reasons.append("ATR supports a realistic 5% move within the holding period")
        if row.breakout_20d_high:
            reasons.append("Trading near a 20-day breakout high")
        if row.rsi_14 > 78:
            reasons.append("Caution: RSI indicates an overbought condition")
        if not reasons:
            reasons.append("Mixed technical setup with no dominant driver")

        return FivePercentPrediction(
            symbol=row.symbol,
            probability_score=probability_score,
            ai_score=ai_score,
            reasons=reasons[:6],
            component_scores=components,
            entry_price=entry_price,
            target_price=target_price,
            stop_loss_price=stop_loss_price,
            expected_return_pct=self.target_pct,
            risk_reward_ratio=risk_reward_ratio,
        )
