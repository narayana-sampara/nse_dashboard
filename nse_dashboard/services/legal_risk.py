from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SEVERITY_POINTS = {
    "TRADING_SUSPENSION": 40.0,
    "DEFAULT_OR_RATING_DOWNGRADE": 40.0,
    "SEBI_PENALTY": 35.0,
    "AUDITOR_QUALIFICATION": 30.0,
    "MATERIAL_LITIGATION": 25.0,
    "AUDITOR_RESIGNATION": 20.0,
    "EXCHANGE_NON_COMPLIANCE": 15.0,
    "PROMOTER_PLEDGE_INCREASE": 12.0,
    "MANAGEMENT_RESIGNATION": 8.0,
    "BOARD_MEETING": 0.0,
    "OTHER_REGULATORY": 5.0,
}


class LegalRiskService:
    version = "legal-v1"

    def score(
        self,
        events: list[dict[str, Any]],
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        contributions: dict[str, float] = {}
        flags: dict[str, bool] = {}
        total = 0.0
        for index, event in enumerate(events):
            event_type = str(event.get("event_type", "OTHER_REGULATORY")).upper()
            event_at = _datetime(event.get("event_at") or event.get("published_at"))
            age_days = max(0, (as_of - event_at).days)
            confidence = max(0.0, min(1.0, float(event.get("confidence", 1))))
            resolved = bool(event.get("resolved", False))
            points = SEVERITY_POINTS.get(event_type, 5.0)
            points *= _recency_multiplier(age_days)
            points *= confidence
            if resolved and event_type != "SEBI_PENALTY":
                points *= 0.25
            key = str(event.get("id", f"{event_type}:{index}"))
            contributions[key] = round(points, 2)
            total += points
            flags[event_type.lower()] = True
        quotient = round(min(100.0, total), 2)
        return {
            "risk_quotient": quotient,
            "risk_flag": (
                "High" if quotient >= 70 else "Medium" if quotient >= 35 else "Low"
            ),
            "coverage": "FULL",
            "features": {
                **flags,
                "event_count": len(events),
                "latest_event_at": max(
                    (
                        str(event.get("event_at") or event.get("published_at"))
                        for event in events
                    ),
                    default=None,
                ),
                "scoring_version": self.version,
            },
            "contributions": contributions,
        }


def _recency_multiplier(age_days: int) -> float:
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.75
    if age_days <= 180:
        return 0.5
    if age_days <= 365:
        return 0.25
    return 0.0


def _datetime(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
