from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StockFeatureRow:
    """Point-in-time technical features for one symbol, computed without look-ahead."""

    symbol: str
    as_of: str
    close: float
    company_name: str | None = None
    sector: str | None = None
    return_1d: float = 0.0
    return_3d: float = 0.0
    return_5d: float = 0.0
    return_20d: float = 0.0
    momentum_5d: float = 0.0
    momentum_20d: float = 0.0
    ema_9: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    rsi_14: float = 50.0
    atr_14: float = 0.0
    volume_ratio: float = 1.0
    avg_volume_20d: float = 0.0
    avg_traded_value_20d: float = 0.0
    volatility: float = 0.0
    gap_pct: float = 0.0
    relative_strength_vs_nifty: float = 0.0
    breakout_20d_high: bool = False
    distance_from_52w_high_pct: float = 0.0
    drawdown_from_recent_high_pct: float = 0.0


@dataclass(slots=True)
class FivePercentPrediction:
    """Baseline (or future ML) model output for one symbol."""

    symbol: str
    probability_score: float
    ai_score: float
    reasons: list[str]
    component_scores: dict[str, float]
    entry_price: float
    target_price: float
    stop_loss_price: float
    expected_return_pct: float
    risk_reward_ratio: float


@dataclass(slots=True)
class Candidate:
    symbol: str
    company_name: str | None
    sector: str | None
    close_price: float
    entry_price: float
    target_price: float
    stop_loss_price: float
    probability_score: float
    ai_score: float
    rank: int
    expected_return_pct: float
    risk_reward_ratio: float
    avg_volume: float
    avg_turnover: float
    volatility: float
    rsi: float
    momentum_5d: float
    momentum_20d: float
    volume_ratio: float
    trend_score: float
    relative_strength_score: float
    breakout_score: float
    risk_score: float
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "sector": self.sector,
            "close_price": round(self.close_price, 2),
            "entry_price": round(self.entry_price, 2),
            "target_price": round(self.target_price, 2),
            "stop_loss_price": round(self.stop_loss_price, 2),
            "probability_score": round(self.probability_score, 2),
            "ai_score": round(self.ai_score, 2),
            "rank": self.rank,
            "expected_return_pct": round(self.expected_return_pct, 2),
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "avg_volume": round(self.avg_volume, 2),
            "avg_turnover": round(self.avg_turnover, 2),
            "volatility": round(self.volatility, 2),
            "rsi": round(self.rsi, 2),
            "momentum_5d": round(self.momentum_5d, 2),
            "momentum_20d": round(self.momentum_20d, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "trend_score": round(self.trend_score, 2),
            "relative_strength_score": round(self.relative_strength_score, 2),
            "breakout_score": round(self.breakout_score, 2),
            "risk_score": round(self.risk_score, 2),
            "reasons": self.reasons,
        }


@dataclass(slots=True)
class Trade:
    symbol: str
    entry_date: str
    exit_date: str | None
    entry_price: float
    exit_price: float | None
    target_price: float
    stop_loss_price: float
    result: str
    return_pct: float
    capital_before: float
    capital_after: float
    holding_days: int
    exit_reason: str
    probability_score: float | None = None
    ai_score: float | None = None


@dataclass(slots=True)
class PaperTrade:
    id: int | None
    signal_id: str | None
    symbol: str
    entry_date: str
    entry_price: float
    target_price: float
    stop_loss_price: float
    current_price: float | None
    status: str
    exit_date: str | None = None
    exit_price: float | None = None
    return_pct: float | None = None
    capital_before: float = 0.0
    capital_after: float | None = None
    exit_reason: str | None = None
