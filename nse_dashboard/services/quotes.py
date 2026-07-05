from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nse_dashboard.core.json import json_ready

MAX_SYMBOLS = 30
QUOTE_CACHE_SECONDS = 15


class StockQuoteService:
    """Fetch Yahoo Finance quotes through the backend and persist fresh batches."""

    def __init__(
        self,
        adapter: Any,
        snapshots: Any,
        cache: Any | None = None,
        cache_seconds: int = QUOTE_CACHE_SECONDS,
    ) -> None:
        self.adapter = adapter
        self.snapshots = snapshots
        self.cache = cache
        self.cache_seconds = cache_seconds

    def prices(self, requested: str | None = None) -> dict[str, Any]:
        symbols = normalize_symbols(requested)
        if not symbols:
            raise ValueError("No valid symbols supplied")
        cache_key = f"quotes:stock-prices:v1:{','.join(symbols)}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        quotes = self.adapter.quotes(symbols)
        result = json_ready(
            {
                "prices": quotes,
                "symbols": symbols,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": self.adapter.name,
            }
        )
        saver = getattr(self.snapshots, "save_market_quotes", None)
        if saver:
            saver(result)
        if self.cache is not None:
            self.cache.set(cache_key, result, self.cache_seconds)
        return result


def normalize_symbols(value: str | None) -> list[str]:
    requested = value.split(",") if value else []
    symbols = [_normalize_symbol(item) for item in requested]
    if not symbols:
        symbols = [
            "SUZLON.NS",
            "RVNL.NS",
            "NHPC.NS",
            "IRFC.NS",
            "IDFCFIRSTB.NS",
            "HFCL.NS",
            "NATIONALUM.NS",
            "MRPL.NS",
            "TRIDENT.NS",
            "GSFC.NS",
        ]
    return list(dict.fromkeys(symbol for symbol in symbols if symbol is not None))[
        :MAX_SYMBOLS
    ]


def _normalize_symbol(value: str) -> str | None:
    symbol = value.strip().upper()
    if not symbol:
        return None
    if not all(character.isalnum() or character in "&.-" for character in symbol):
        return None
    if "." in symbol:
        return symbol
    return f"{symbol}.NS"
