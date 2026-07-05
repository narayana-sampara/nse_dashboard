from __future__ import annotations

import json
from datetime import datetime
from urllib.request import Request
from zoneinfo import ZoneInfo

from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.infrastructure.indian_api import IndianStockMarketClient


IST = ZoneInfo("Asia/Kolkata")


class FakeResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_indian_api_context_is_called_once_per_day() -> None:
    calls: list[Request] = []

    def opener(request: Request, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request)
        return FakeResponse(
            {
                "trending_stocks": {
                    "top_gainers": [
                        {"ticker_id": "TCS", "company_name": "TCS", "price": "3900.5"}
                    ],
                    "top_losers": [
                        {"ticker_id": "INFY.NS", "percent_change": "-1.2%"}
                    ],
                }
            }
        )

    client = IndianStockMarketClient(
        base_url="https://indianapi.in",
        api_key="test-key",
        cache=MemoryTtlCache(),
        opener=opener,
        clock=lambda: datetime(2026, 6, 23, 10, 0, tzinfo=IST),
    )

    first = client.daily_market_context()
    second = client.daily_market_context()

    assert len(calls) == 1
    assert first == second
    assert first is not None
    assert first["top_gainers"][0]["symbol"] == "TCS.NS"
    assert first["top_losers"][0]["symbol"] == "INFY.NS"
    assert calls[0].get_header("Authorization") == "Bearer test-key"


def test_indian_api_failed_attempt_is_not_retried_same_day() -> None:
    calls = 0

    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    client = IndianStockMarketClient(
        base_url="https://indianapi.in",
        api_key="test-key",
        cache=MemoryTtlCache(),
        opener=opener,
        clock=lambda: datetime(2026, 6, 23, 10, 0, tzinfo=IST),
    )

    assert client.daily_market_context() is None
    assert client.daily_market_context() is None
    assert calls == 1
