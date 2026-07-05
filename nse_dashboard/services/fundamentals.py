from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _bounded(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _linear(value: float | None, poor: float, good: float, *, inverse: bool = False) -> float:
    if value is None:
        return 0.0
    if good == poor:
        return 0.0
    score = (float(value) - poor) / (good - poor) * 100
    if inverse:
        score = 100 - score
    return _bounded(score)


class FundamentalService:
    """Normalize financial metrics into a transparent zero-to-100 score."""

    version = "fundamental-v1"

    def score(self, metrics: dict[str, Any]) -> dict[str, Any]:
        values = {
            "roe_pct": _number(metrics.get("roe_pct")),
            "roce_pct": _number(metrics.get("roce_pct")),
            "operating_margin_pct": _number(metrics.get("operating_margin_pct")),
            "net_margin_pct": _number(metrics.get("net_margin_pct")),
            "ttm_revenue_growth_pct": _number(metrics.get("ttm_revenue_growth_pct")),
            "ttm_net_profit_growth_pct": _number(metrics.get("ttm_net_profit_growth_pct")),
            "fcf_growth_pct": _number(metrics.get("fcf_growth_pct")),
            "debt_to_equity": _number(metrics.get("debt_to_equity")),
            "current_ratio": _number(metrics.get("current_ratio")),
            "promoter_holding_change_qoq_pct": _number(
                metrics.get("promoter_holding_change_qoq_pct")
            ),
            "pe_ratio": _number(metrics.get("pe_ratio")),
            "sector_pe_ratio": _number(metrics.get("sector_pe_ratio")),
        }
        component_scores = {
            "roe": _linear(values["roe_pct"], 5, 25),
            "roce": _linear(values["roce_pct"], 6, 30),
            "operating_margin": _linear(values["operating_margin_pct"], 5, 30),
            "net_margin": _linear(values["net_margin_pct"], 2, 20),
            "revenue_growth": _linear(values["ttm_revenue_growth_pct"], -5, 25),
            "profit_growth": _linear(values["ttm_net_profit_growth_pct"], -10, 30),
            "fcf_growth": _linear(values["fcf_growth_pct"], -10, 30),
            "leverage": _linear(values["debt_to_equity"], 2.0, 0.0),
            "liquidity": _linear(values["current_ratio"], 0.7, 2.0),
            "promoter_change": _linear(
                values["promoter_holding_change_qoq_pct"], -2.0, 1.0
            ),
        }
        weights = {
            "roe": 0.15,
            "roce": 0.15,
            "operating_margin": 0.10,
            "net_margin": 0.10,
            "revenue_growth": 0.10,
            "profit_growth": 0.10,
            "fcf_growth": 0.10,
            "leverage": 0.10,
            "liquidity": 0.05,
            "promoter_change": 0.05,
        }
        contributions = {
            name: round(component_scores[name] * weight, 2)
            for name, weight in weights.items()
        }
        base_score = sum(contributions.values())
        value_trap_penalty = self.value_trap_penalty(
            values["pe_ratio"], values["sector_pe_ratio"]
        )
        score = round(_bounded(base_score - value_trap_penalty), 2)
        available = sum(value is not None for value in values.values())
        return {
            "score": score,
            "grade": fundamental_grade(score),
            "coverage": "FULL" if available >= 8 else "PARTIAL",
            "features": {
                **values,
                "value_trap_penalty": value_trap_penalty,
                "scoring_version": self.version,
            },
            "contributions": {
                **contributions,
                "value_trap_penalty": -value_trap_penalty,
            },
            "known_at": str(
                metrics.get("known_at") or datetime.now(timezone.utc).isoformat()
            ),
        }

    @staticmethod
    def value_trap_penalty(
        pe_ratio: float | None, sector_pe_ratio: float | None
    ) -> float:
        if (
            pe_ratio is None
            or sector_pe_ratio is None
            or pe_ratio <= 0
            or sector_pe_ratio <= 0
        ):
            return 0.0
        premium = pe_ratio / sector_pe_ratio - 1
        return round(min(15.0, max(0.0, 50 * (premium - 0.30))), 2)


def fundamental_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    if score >= 35:
        return "D"
    return "F"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
