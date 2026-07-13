from __future__ import annotations

from typing import Any

import pytest

from nse_dashboard.domain.snapshots import NullSnapshotRepository
from nse_dashboard.services.bookmarks import BookmarkService
from nse_dashboard.services.quotes import StockQuoteService


class FakeAdapter:
    name = "fake"

    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        return {
            symbol: {"symbol": symbol, "price": self._prices[symbol]}
            for symbol in symbols
            if symbol in self._prices
        }


def _service(prices: dict[str, float]) -> BookmarkService:
    adapter = FakeAdapter(prices)
    snapshots = NullSnapshotRepository()
    quote_service = StockQuoteService(adapter, snapshots, cache=None)
    return BookmarkService(snapshots, quote_service)


def test_follow_creates_bookmark_with_current_price() -> None:
    service = _service({"RELIANCE.NS": 2500.0})
    record = service.follow(1, "RELIANCE.NS")
    assert record["symbol"] == "RELIANCE.NS"
    assert record["bookmark_price"] == 2500.0
    assert record["user_id"] == 1


def test_refollow_resets_price_and_timestamp() -> None:
    service = _service({"RELIANCE.NS": 2500.0})
    first = service.follow(1, "RELIANCE.NS")
    service.quote_service.adapter._prices["RELIANCE.NS"] = 2600.0
    second = service.follow(1, "RELIANCE.NS")
    assert second["bookmark_price"] == 2600.0
    assert second["created_at"] >= first["created_at"]
    assert len(service.list(1)) == 1


def test_unfollow_removes_bookmark() -> None:
    service = _service({"RELIANCE.NS": 2500.0})
    service.follow(1, "RELIANCE.NS")
    assert service.unfollow(1, "RELIANCE.NS") is True
    assert service.list(1) == []


def test_unfollow_missing_bookmark_returns_false() -> None:
    service = _service({"RELIANCE.NS": 2500.0})
    assert service.unfollow(1, "RELIANCE.NS") is False


def test_list_computes_growth_pct() -> None:
    service = _service({"RELIANCE.NS": 2500.0})
    service.follow(1, "RELIANCE.NS")
    service.quote_service.adapter._prices["RELIANCE.NS"] = 2750.0
    service.quote_service.cache = None
    bookmarks = service.list(1)
    assert len(bookmarks) == 1
    assert bookmarks[0]["current_price"] == 2750.0
    assert bookmarks[0]["growth_pct"] == pytest.approx(10.0)


def test_follow_invalid_symbol_raises() -> None:
    service = _service({})
    with pytest.raises(ValueError):
        service.follow(1, "###")
