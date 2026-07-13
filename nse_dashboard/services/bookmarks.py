from __future__ import annotations

from typing import Any

from nse_dashboard.services.quotes import StockQuoteService, _normalize_symbol


class BookmarkNotFoundError(Exception):
    pass


class BookmarkService:
    """Tracks per-user stock bookmarks against live price movement."""

    def __init__(self, repository: Any, quote_service: StockQuoteService) -> None:
        self.repository = repository
        self.quote_service = quote_service

    def follow(self, user_id: int, symbol: str) -> dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        if normalized is None:
            raise ValueError(f"Invalid symbol: {symbol}")
        quote = self.quote_service.prices(normalized)
        price = quote["prices"].get(normalized, {}).get("price")
        if price is None:
            raise ValueError(f"No current price available for {normalized}")
        record = self.repository.save_bookmark(user_id, normalized, price)
        return record

    def unfollow(self, user_id: int, symbol: str) -> bool:
        normalized = _normalize_symbol(symbol)
        if normalized is None:
            raise ValueError(f"Invalid symbol: {symbol}")
        return self.repository.delete_bookmark(user_id, normalized)

    def list(self, user_id: int) -> list[dict[str, Any]]:
        bookmarks = self.repository.list_bookmarks(user_id)
        if not bookmarks:
            return []
        symbols = [bookmark["symbol"] for bookmark in bookmarks]
        quote = self.quote_service.prices(",".join(symbols))
        prices = quote["prices"]
        result = []
        for bookmark in bookmarks:
            current = prices.get(bookmark["symbol"], {}).get("price")
            bookmark_price = bookmark["bookmark_price"]
            growth_pct = (
                round((current - bookmark_price) / bookmark_price * 100, 2)
                if current is not None and bookmark_price
                else None
            )
            result.append(
                {
                    **bookmark,
                    "current_price": current,
                    "growth_pct": growth_pct,
                }
            )
        return result
