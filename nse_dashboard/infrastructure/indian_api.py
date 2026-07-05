from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from nse_dashboard.infrastructure.cache import TtlCache

IST = ZoneInfo("Asia/Kolkata")


class IndianApiError(RuntimeError):
    """The Indian Stock Market API failed or returned unusable data."""


class IndianStockMarketClient:
    """Daily-limited client for Indian API stock market context."""

    name = "Indian Stock Market API"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        cache: TtlCache,
        timeout_seconds: float = 8.0,
        endpoint: str = "/trending",
        opener: Callable[[Request, float], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.endpoint = endpoint
        self._opener = opener or _open_url
        self._clock = clock or (lambda: datetime.now(IST))

    def daily_market_context(self) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        now = self._now()
        call_date = now.date().isoformat()
        data_key = f"indian-api:stock-market-context:{call_date}"
        cached = self.cache.get(data_key)
        if cached is not None:
            return cached

        ttl_seconds = _seconds_until_next_day(now)
        attempt_key = f"indian-api:stock-market-context-attempt:{call_date}"
        attempt = {
            "attempted_at": now.isoformat(),
            "endpoint": self.endpoint,
        }
        if not self.cache.set_if_absent(attempt_key, attempt, ttl_seconds):
            return None

        try:
            payload = self._fetch_json()
            context = self._normalize_trending(payload, now)
        except IndianApiError:
            return None

        self.cache.set(data_key, context, ttl_seconds)
        return context

    def _fetch_json(self) -> Any:
        request = Request(
            urljoin(self.base_url, self.endpoint.lstrip("/")),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Key": str(self.api_key),
            },
            method="GET",
        )
        try:
            with self._opener(request, self.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                if status >= 400:
                    raise IndianApiError(f"Indian API returned HTTP {status}")
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise IndianApiError("Indian API request failed") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IndianApiError("Indian API returned invalid JSON") from exc

    def _normalize_trending(self, payload: Any, now: datetime) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IndianApiError("Indian API trending payload must be an object")
        stocks = payload.get("trending_stocks")
        if not isinstance(stocks, dict):
            raise IndianApiError("Indian API trending payload has no trending_stocks")

        top_gainers = _normalize_items(stocks.get("top_gainers"))
        top_losers = _normalize_items(stocks.get("top_losers"))
        return {
            "provider": self.name,
            "endpoint": self.endpoint,
            "fetched_at": now.isoformat(),
            "call_date": now.date().isoformat(),
            "top_gainers": top_gainers,
            "top_losers": top_losers,
        }

    def _now(self) -> datetime:
        now = self._clock()
        return now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)


def _open_url(request: Request, timeout_seconds: float) -> Any:
    return urlopen(request, timeout=timeout_seconds)


def _seconds_until_next_day(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).date()
    next_midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=IST)
    return max(60, int((next_midnight - now.astimezone(IST)).total_seconds()))


def _normalize_items(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    items = []
    for value in values:
        if not isinstance(value, dict):
            continue
        symbol = _normalize_symbol(
            value.get("ticker_id") or value.get("ticker") or value.get("ric")
        )
        if not symbol:
            continue
        items.append(
            {
                "symbol": symbol,
                "company_name": value.get("company_name") or value.get("company"),
                "price": _as_float(value.get("price")),
                "percent_change": _as_float(value.get("percent_change")),
                "overall_rating": value.get("overall_rating"),
                "short_term_trends": value.get("short_term_trends"),
                "long_term_trends": value.get("long_term_trends"),
            }
        )
    return items


def _normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None
    symbol = str(value).strip().upper()
    if not symbol:
        return None
    for suffix in (".NS", ".NSE", ".BO", ".BSE"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
            break
    return f"{symbol}.NS"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace("%", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
