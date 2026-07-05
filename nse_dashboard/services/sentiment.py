from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


class SentimentService:
    version = "sentiment-v1"

    def aggregate(
        self,
        items: list[dict[str, Any]],
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        numerator = 0.0
        denominator = 0.0
        contributions: dict[str, float] = {}
        for index, item in enumerate(items):
            published_at = _datetime(item["published_at"])
            age_days = max(0.0, (as_of - published_at).total_seconds() / 86400)
            decay = news_decay(age_days)
            raw = max(-1.0, min(1.0, float(item.get("raw_sentiment", 0))))
            relevance = max(0.0, min(1.0, float(item.get("relevance", 0))))
            source_quality = max(
                0.0, min(1.0, float(item.get("source_quality", 0.5)))
            )
            weight = relevance * source_quality * decay
            numerator += raw * weight
            denominator += weight
            contributions[str(item.get("id", index))] = round(raw * weight, 6)
        composite = numerator / denominator if denominator else 0.0
        score = round(50 * (composite + 1), 2)
        return {
            "score": score,
            "composite_score": round(composite, 4),
            "trend": sentiment_trend(score),
            "coverage": "FULL" if denominator >= 1 else "PARTIAL",
            "effective_article_weight": round(denominator, 4),
            "features": {
                "article_count": len(items),
                "effective_article_weight": round(denominator, 4),
                "composite_score": round(composite, 4),
                "scoring_version": self.version,
            },
            "contributions": contributions,
        }


def news_decay(age_days: float) -> float:
    if age_days <= 5:
        return 1.0
    return math.exp(-math.log(2) * (age_days - 5) / 2)


def sentiment_trend(score: float) -> str:
    if score >= 60:
        return "Bullish"
    if score <= 40:
        return "Bearish"
    return "Neutral"


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
