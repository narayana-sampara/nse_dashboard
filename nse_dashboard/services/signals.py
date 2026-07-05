from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from nse_dashboard.domain.market_data import MarketDataAdapter
from nse_dashboard.domain.snapshots import NullSnapshotRepository, SnapshotRepository
from nse_dashboard.infrastructure.cache import TtlCache
from sector_map import SECTOR_MAP, display_name, get_sector
from strategies.composite import CompositeTechnicalStrategy
from nse_dashboard.trading.indicators import entry_indicators, market_regime


class SignalService:
    """Compute and rank signals without knowing which provider supplies the data."""

    dashboard_cache_key = "signals:dashboard:v2"
    benchmark_symbol = "^CNX100"

    def __init__(
        self,
        adapter: MarketDataAdapter,
        cache: TtlCache,
        cache_seconds: int = 900,
        default_period: str = "1y",
        snapshots: SnapshotRepository | None = None,
    ) -> None:
        self.adapter = adapter
        self.cache = cache
        self.cache_seconds = cache_seconds
        self.default_period = default_period
        self.snapshots = snapshots or NullSnapshotRepository()
        self.strategy = CompositeTechnicalStrategy()
        self._scan_lock = Lock()

    def evaluate(
        self,
        symbol: str,
        strategy_name: str = "composite_technical",
        period: str | None = None,
    ) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        if strategy_name != self.strategy.name:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        if not symbol.endswith(".NS"):
            raise ValueError("Use an NSE ticker ending in .NS")
        frame = self.adapter.history(symbol, period or self.default_period)
        result = {
            "symbol": symbol,
            "name": display_name(symbol),
            "sector": get_sector(symbol),
            "source": self.adapter.name,
            "strategy": self.strategy.name,
            **self.strategy.evaluate(frame),
        }
        self.snapshots.save_signal(result)
        return result

    def scan_market(self, force: bool = False) -> dict[str, Any]:
        if not force:
            cached = self.cache.get(self.dashboard_cache_key)
            if cached is not None:
                return cached

        with self._scan_lock:
            if not force:
                cached = self.cache.get(self.dashboard_cache_key)
                if cached is not None:
                    return cached

            histories = self.ingest_market()
            result = self.compute_market_scan(histories)
            self.cache.set(self.dashboard_cache_key, result, self.cache_seconds)
            self.snapshots.save_market_scan(result)
            return result

    def ingest_market(self) -> dict[str, Any]:
        """Fetch the configured universe without performing signal computation."""
        return self.adapter.market_history([*SECTOR_MAP, self.benchmark_symbol], self.default_period)

    def compute_market_scan(self, histories: dict[str, Any]) -> dict[str, Any]:
        """Compute a dashboard result from an already-ingested market-data batch."""
        symbols = list(SECTOR_MAP)
        try:
            regime = market_regime(histories[self.benchmark_symbol])
        except (KeyError, TypeError, ValueError):
            regime = {
                "state": "UNAVAILABLE", "as_of": None,
                "maximum_exposure_pct": 0, "risk_per_trade_pct": 0,
            }
        ranked: dict[str, list[dict[str, Any]]] = defaultdict(list)
        failures: list[str] = []

        for symbol in symbols:
            try:
                outcome = self.strategy.evaluate(histories[symbol])
                try:
                    trigger = entry_indicators(histories[symbol])
                except (KeyError, TypeError, ValueError):
                    trigger = None
                if trigger is not None:
                    outcome["monthly_entry"] = trigger
                    outcome["signal_state"] = (
                        "BUY_READY" if trigger["entry_ready"] and regime["state"] != "RISK_OFF"
                        else "WATCHLIST"
                    )
                ranked[SECTOR_MAP[symbol]].append(
                    {
                        "symbol": symbol,
                        "name": display_name(symbol),
                        "sector": SECTOR_MAP[symbol],
                        **outcome,
                    }
                )
            except (KeyError, TypeError, ValueError):
                failures.append(symbol)

        sectors = []
        for sector in sorted(set(SECTOR_MAP.values())):
            candidates = ranked.get(sector, [])
            sectors.append(
                {
                    "name": sector,
                    "buys": sorted(
                        (item for item in candidates if item["signal"] == "BUY"),
                        key=lambda item: item["score"],
                        reverse=True,
                    )[:5],
                    "sells": sorted(
                        (item for item in candidates if item["signal"] == "SELL"),
                        key=lambda item: item["score"],
                    )[:5],
                    "scanned": len(candidates),
                }
            )

        all_items = [item for items in ranked.values() for item in items]
        dates = [item["as_of"] for item in all_items]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_date": max(dates) if dates else None,
            "source": self.adapter.name,
            "strategy": self.strategy.name,
            "monthly_strategy": "conservative_nse_monthly:2.0.0",
            "regime": regime,
            "universe_size": len(symbols),
            "stocks_scored": len(all_items),
            "failures": failures,
            "sectors": sectors,
        }

    def history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        symbol = symbol.strip().upper()
        if not symbol.endswith(".NS"):
            raise ValueError("Use an NSE ticker ending in .NS")
        return self.snapshots.signal_history(symbol, limit)
