from __future__ import annotations

import json
from unittest.mock import patch
from urllib.request import Request

import pandas as pd
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.infrastructure.yahoo import YahooFinanceAdapter
from nse_dashboard.services.quotes import StockQuoteService, normalize_symbols
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeAdapter, FakeSnapshots


class QuoteAdapter(FakeAdapter):
    name = "Yahoo Finance"

    def __init__(self) -> None:
        super().__init__()
        self.quote_calls = 0

    def quotes(self, symbols: list[str]):
        self.quote_calls += 1
        return {
            symbol: {
                "symbol": symbol,
                "name": symbol,
                "currency": "INR",
                "price": 100.0,
                "close": 100.0,
                "change": 1.0,
                "change_pct": 1.0,
                "previous_close": 99.0,
                "day_high": 101.0,
                "day_low": 98.0,
                "as_of": "01 Jul, 03:30 PM",
                "market_time": "2026-07-01T10:00:00+00:00",
                "market_state": "REGULAR",
                "price_basis": "INTRADAY",
            }
            for symbol in symbols
        }


class QuoteSnapshots(FakeSnapshots):
    def __init__(self) -> None:
        super().__init__()
        self.quotes = []

    def save_market_quotes(self, snapshot):
        self.quotes.append(snapshot)
        return len(snapshot["prices"])


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


def test_quote_symbols_are_normalized_and_limited() -> None:
    symbols = normalize_symbols("reliance, tcs.ns, bad symbol, 500325.bo")

    assert symbols == ["RELIANCE.NS", "TCS.NS", "500325.BO"]


def test_quote_service_uses_cache_and_persists_fresh_yahoo_batch() -> None:
    adapter = QuoteAdapter()
    snapshots = QuoteSnapshots()
    service = StockQuoteService(adapter, snapshots, cache=MemoryTtlCache())

    first = service.prices("reliance,tcs")
    second = service.prices("RELIANCE.NS,TCS.NS")

    assert adapter.quote_calls == 1
    assert first == second
    assert first["source"] == "Yahoo Finance"
    assert set(first["prices"]) == {"RELIANCE.NS", "TCS.NS"}
    assert snapshots.quotes == [first]


def test_stock_prices_api_uses_backend_yahoo_adapter_and_snapshot_repository() -> None:
    adapter = QuoteAdapter()
    snapshots = QuoteSnapshots()
    service = SignalService(adapter, MemoryTtlCache(), snapshots=snapshots)

    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.get("/api/v1/stock-prices?symbols=reliance")

    assert response.status_code == 200
    assert response.json()["prices"]["RELIANCE.NS"]["price"] == 100.0
    assert adapter.quote_calls == 1
    assert len(snapshots.quotes) == 1


def test_yahoo_quote_adapter_parses_quote_response() -> None:
    calls: list[Request] = []

    def opener(request: Request, timeout: float) -> FakeResponse:
        del timeout
        calls.append(request)
        return FakeResponse(
            {
                "quoteResponse": {
                    "result": [
                        {
                            "symbol": "RELIANCE.NS",
                            "shortName": "Reliance Industries",
                            "currency": "INR",
                            "marketState": "REGULAR",
                            "regularMarketPrice": 1420.5,
                            "regularMarketChange": 12.5,
                            "regularMarketChangePercent": 0.89,
                            "regularMarketTime": 1782900000,
                            "regularMarketPreviousClose": 1408.0,
                            "regularMarketDayHigh": 1430.0,
                            "regularMarketDayLow": 1401.0,
                        }
                    ]
                }
            }
        )

    quotes = YahooFinanceAdapter(opener=opener).quotes(["RELIANCE.NS"])

    assert len(calls) == 1
    assert "query1.finance.yahoo.com" in calls[0].full_url
    assert quotes["RELIANCE.NS"]["name"] == "Reliance Industries"
    assert quotes["RELIANCE.NS"]["price_basis"] == "INTRADAY"


def test_yahoo_quote_adapter_falls_back_to_download_when_quote_endpoint_fails() -> None:
    def opener(request: Request, timeout: float) -> FakeResponse:
        del request, timeout
        response = FakeResponse({})
        response.status = 502
        return response

    intraday = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 104.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 103.0],
            "Volume": [1000, 1500],
        },
        index=pd.date_range("2026-07-01 09:15", periods=2, freq="min"),
    )
    daily = pd.DataFrame(
        {
            "Open": [95.0, 100.0],
            "High": [100.0, 104.0],
            "Low": [94.0, 99.0],
            "Close": [100.0, 103.0],
            "Volume": [10_000, 12_000],
        },
        index=pd.date_range("2026-06-30", periods=2, freq="D"),
    )

    with patch(
        "nse_dashboard.infrastructure.yahoo.yf.download",
        side_effect=[intraday, daily],
    ):
        quotes = YahooFinanceAdapter(opener=opener).quotes(["RELIANCE.NS"])

    assert quotes["RELIANCE.NS"]["price"] == 103.0
    assert quotes["RELIANCE.NS"]["previous_close"] == 100.0
    assert quotes["RELIANCE.NS"]["change_pct"] == 3.0
    assert quotes["RELIANCE.NS"]["price_basis"] == "LATEST"
