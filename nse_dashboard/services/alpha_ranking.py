from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nse_dashboard.core.json import json_ready
from nse_dashboard.domain.alpha import AlphaFeatureSet, FactorInput, exchange_for_symbol
from nse_dashboard.services.fundamentals import fundamental_grade
from nse_dashboard.services.sentiment import sentiment_trend


BASE_WEIGHTS = {
    "technical": 0.30,
    "options": 0.20,
    "fundamental": 0.30,
    "sentiment": 0.10,
}


class AlphaRankingService:
    name = "multi_factor_alpha"
    version = "1.0.0"
    feature_set_version = "alpha-features-v1"

    def __init__(self, snapshots: Any) -> None:
        self.snapshots = snapshots

    def score_candidate(
        self,
        candidate: dict[str, Any],
        features: AlphaFeatureSet,
        *,
        require_fundamentals: bool = True,
    ) -> dict[str, Any] | None:
        technical_score = _candidate_score(candidate)
        factors = {
            "technical": FactorInput(
                technical_score,
                "FULL",
                features=dict(candidate.get("features", {})),
                contributions=_technical_contributions(candidate, technical_score),
            ),
            "options": features.options,
            "fundamental": features.fundamental,
            "sentiment": features.sentiment,
        }
        if require_fundamentals and not factors["fundamental"].usable:
            return None
        available = {name: value for name, value in factors.items() if value.usable}
        available_weight = sum(BASE_WEIGHTS[name] for name in available)
        minimum_weight = 0.60 if require_fundamentals else 0.30
        if available_weight < minimum_weight:
            return None

        effective_weights = {
            name: BASE_WEIGHTS[name] / available_weight for name in available
        }
        positive_score = sum(
            effective_weights[name] * float(value.score or 0)
            for name, value in available.items()
        )
        legal_known = features.legal.score is not None
        legal_risk = max(0.0, min(100.0, float(features.legal.score or 0)))
        legal_credit = 10.0 if legal_known else 0.0
        legal_penalty = 0.10 * legal_risk
        combined_score = max(
            0.0, min(100.0, 0.90 * positive_score + legal_credit - legal_penalty)
        )
        factor_contributions: dict[str, Any] = {}
        feature_contributions: dict[str, float] = {}
        for name, value in factors.items():
            effective = effective_weights.get(name, 0.0)
            points = 0.90 * effective * float(value.score or 0)
            factor_contributions[name] = {
                "raw_score": value.score,
                "base_weight": BASE_WEIGHTS[name],
                "effective_weight": round(effective, 6),
                "points": round(points, 2),
                "coverage": value.coverage,
            }
            for feature, contribution in value.contributions.items():
                feature_contributions[f"{name}.{feature}"] = round(
                    float(contribution) * 0.90 * effective, 2
                )
        for feature, deduction in features.legal.contributions.items():
            feature_contributions[f"legal.{feature}"] = -abs(float(deduction))

        grade = (
            fundamental_grade(float(features.fundamental.score))
            if features.fundamental.score is not None
            else "Unknown"
        )
        sentiment_label = (
            sentiment_trend(float(features.sentiment.score))
            if features.sentiment.score is not None
            else "Unknown"
        )
        legal_flag = (
            "Unknown"
            if not legal_known
            else "High"
            if legal_risk >= 70
            else "Medium"
            if legal_risk >= 35
            else "Low"
        )
        top_reasons = [
            _reason(name, value)
            for name, value in sorted(
                feature_contributions.items(),
                key=lambda item: abs(item[1]),
                reverse=True,
            )[:5]
        ]
        entry = candidate.get("entry", {})
        proposed_stop = entry.get("proposed_stop") or candidate.get("features", {}).get(
            "proposed_stop"
        )
        result = {
            **candidate,
            "exchange": exchange_for_symbol(str(candidate["symbol"])),
            "combined_score": round(combined_score, 2),
            "fundamental_grade": grade,
            "sentiment_trend": sentiment_label,
            "legal_risk_flag": legal_flag,
            "legal_risk_quotient": round(legal_risk, 2) if legal_known else None,
            "legal_credit": legal_credit,
            "legal_penalty": round(-legal_penalty, 2),
            "coverage_status": (
                "FULL"
                if all(factor.usable for factor in factors.values()) and legal_known
                else "PARTIAL"
            ),
            "factor_contributions": factor_contributions,
            "feature_contributions": feature_contributions,
            "feature_breakdown": {
                "technical": factors["technical"].features,
                "options": features.options.features,
                "fundamentals": features.fundamental.features,
                "sentiment": features.sentiment.features,
                "legal": features.legal.features,
            },
            "top_reasons": top_reasons,
            "atr_stop_loss": proposed_stop,
            "sector_exposure_cap_pct": 20.0,
            "entry_allowed": bool(candidate.get("entry_allowed", True))
            and legal_risk < 70,
            "disclaimer": "Research signal only; not investment advice.",
        }
        return json_ready(result)

    def build(
        self,
        prediction_snapshot: dict[str, Any],
        *,
        horizon: str,
        horizon_months: int | None = None,
        require_fundamentals: bool = True,
        limit: int = 20,
        fundamental_grades: set[str] | None = None,
        exclude_legal_risks: bool = False,
    ) -> dict[str, Any]:
        candidates = [
            dict(item)
            for sector in prediction_snapshot.get("sectors", [])
            for item in sector.get("picks", [])
        ]
        symbols = [str(item["symbol"]) for item in candidates]
        loader = getattr(self.snapshots, "latest_alpha_features", None)
        raw_features = loader(symbols) if loader and symbols else {}
        scored = []
        excluded = 0
        for candidate in candidates:
            features = _feature_set(raw_features.get(candidate["symbol"], {}))
            item = self.score_candidate(
                candidate, features, require_fundamentals=require_fundamentals
            )
            if item is None:
                excluded += 1
                continue
            if fundamental_grades and item["fundamental_grade"] not in fundamental_grades:
                continue
            if exclude_legal_risks and item["legal_risk_flag"] == "High":
                continue
            scored.append(item)
        scored.sort(
            key=lambda item: (
                -float(item["combined_score"]),
                -float(item.get("target_probability", 0)),
                -float(item.get("average_traded_value", 0)),
                str(item["symbol"]),
            )
        )
        picks = scored[:limit]
        sectors: dict[str, list[dict[str, Any]]] = {}
        for overall_rank, item in enumerate(picks, start=1):
            item["overall_rank"] = overall_rank
            group = sectors.setdefault(str(item.get("sector", "Unknown")), [])
            item["sector_rank"] = len(group) + 1
            group.append(item)
        result = {
            "schema_version": "2.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_date": prediction_snapshot.get("market_date"),
            "horizon": horizon,
            "horizon_months": horizon_months,
            "model": {
                "name": self.name,
                "version": self.version,
                "feature_set_version": self.feature_set_version,
            },
            "base_weights": {
                **BASE_WEIGHTS,
                "legal_max_deduction": 0.10,
            },
            "universe_size": prediction_snapshot.get("universe_size", len(candidates)),
            "eligible_stocks": len(scored),
            "excluded_for_coverage": excluded,
            "predictions_count": len(picks),
            "picks": picks,
            "sectors": [
                {"name": sector, "picks": values}
                for sector, values in sorted(sectors.items())
            ],
            "disclaimer": (
                "Rankings are quantitative research signals, not investment advice "
                "or a guarantee of returns."
            ),
        }
        return json_ready(result)

    def generate(self, horizon: str, horizon_months: int = 1) -> dict[str, Any]:
        if horizon == "weekly":
            source = self.snapshots.latest_weekly_predictions(None, 20)
        elif horizon == "monthly":
            source = self.snapshots.latest_monthly_predictions(
                horizon_months, None, 20
            )
        else:
            raise ValueError("horizon must be weekly or monthly")
        result = self.build(
            source,
            horizon=horizon,
            horizon_months=horizon_months if horizon == "monthly" else None,
        )
        saver = getattr(self.snapshots, "save_alpha_ranking", None)
        if saver:
            saver(result)
        return result

    def latest(
        self,
        horizon: str,
        horizon_months: int = 1,
        *,
        limit: int = 20,
        fundamental_grades: set[str] | None = None,
        exclude_legal_risks: bool = False,
    ) -> dict[str, Any]:
        loader = getattr(self.snapshots, "latest_alpha_ranking", None)
        persisted = loader(horizon, horizon_months) if loader else None
        if persisted:
            result = dict(persisted)
            picks = list(result.get("picks", []))
            if fundamental_grades:
                picks = [
                    item
                    for item in picks
                    if item.get("fundamental_grade") in fundamental_grades
                ]
            if exclude_legal_risks:
                picks = [
                    item for item in picks if item.get("legal_risk_flag") != "High"
                ]
            result["picks"] = picks[:limit]
            result["predictions_count"] = len(result["picks"])
            return result
        if horizon == "weekly":
            source = self.snapshots.latest_weekly_predictions(None, 20)
        else:
            source = self.snapshots.latest_monthly_predictions(
                horizon_months, None, 20
            )
        return self.build(
            source,
            horizon=horizon,
            horizon_months=horizon_months if horizon == "monthly" else None,
            limit=limit,
            fundamental_grades=fundamental_grades,
            exclude_legal_risks=exclude_legal_risks,
        )


def _candidate_score(candidate: dict[str, Any]) -> float:
    return max(
        0.0,
        min(
            100.0,
            float(candidate.get("ranking_score", candidate.get("score", 0))),
        ),
    )


def _technical_contributions(
    candidate: dict[str, Any], technical_score: float
) -> dict[str, float]:
    breakdown = candidate.get("score_breakdown")
    if isinstance(breakdown, dict) and breakdown:
        total = sum(max(0.0, float(value)) for value in breakdown.values()) or 1.0
        return {
            str(name): technical_score * max(0.0, float(value)) / total
            for name, value in breakdown.items()
        }
    return {"technical_model": technical_score}


def _feature_set(raw: dict[str, Any]) -> AlphaFeatureSet:
    return AlphaFeatureSet(
        fundamental=_factor(raw.get("fundamental"), score_key="score"),
        sentiment=_factor(raw.get("sentiment"), score_key="score"),
        legal=_factor(raw.get("legal"), score_key="risk_quotient"),
        options=_factor(raw.get("options"), score_key="score"),
    )


def _factor(raw: Any, *, score_key: str) -> FactorInput:
    if not isinstance(raw, dict):
        return FactorInput(None)
    score = raw.get(score_key)
    return FactorInput(
        float(score) if score is not None else None,
        str(raw.get("coverage", "FULL")),
        features=dict(raw.get("features", raw)),
        contributions={
            str(name): float(value)
            for name, value in raw.get("contributions", {}).items()
        },
    )


def _reason(name: str, value: float) -> str:
    label = name.replace(".", " ").replace("_", " ").title()
    action = "contributed" if value >= 0 else "deducted"
    return f"{label} {action} {abs(value):.2f} points"
