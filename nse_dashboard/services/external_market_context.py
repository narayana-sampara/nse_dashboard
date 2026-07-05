from __future__ import annotations

from typing import Any, Protocol


class ExternalMarketContextProvider(Protocol):
    def daily_market_context(self) -> dict[str, Any] | None: ...


def summarize_external_context(
    context: dict[str, Any] | None, fallback_source: str
) -> dict[str, Any]:
    if not context:
        return {
            "provider": fallback_source,
            "status": "not_configured",
            "fallback_source": fallback_source,
        }
    return {
        "provider": context.get("provider", "External market context"),
        "status": "active",
        "endpoint": context.get("endpoint"),
        "fetched_at": context.get("fetched_at"),
        "call_date": context.get("call_date"),
        "top_gainers_count": len(context.get("top_gainers", [])),
        "top_losers_count": len(context.get("top_losers", [])),
    }


def annotate_candidate_with_context(
    candidate: dict[str, Any], context: dict[str, Any] | None
) -> None:
    if not context:
        return
    symbol = str(candidate.get("symbol", "")).upper()
    for bucket, label in (
        ("top_gainers", "external top gainer"),
        ("top_losers", "external top loser"),
    ):
        match = _find_symbol(context.get(bucket, []), symbol)
        if match is None:
            continue
        candidate["external_market_context"] = {
            "provider": context.get("provider", "External market context"),
            "bucket": bucket,
            "price": match.get("price"),
            "percent_change": match.get("percent_change"),
            "overall_rating": match.get("overall_rating"),
            "short_term_trends": match.get("short_term_trends"),
            "long_term_trends": match.get("long_term_trends"),
        }
        reasons = candidate.setdefault("reasons", [])
        if isinstance(reasons, list) and label not in reasons:
            reasons.append(label)
            candidate["reasons"] = reasons[:4]
        return


def _find_symbol(items: Any, symbol: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and str(item.get("symbol", "")).upper() == symbol:
            return item
    return None
