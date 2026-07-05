from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from nse_dashboard.domain.market_data import BrokerInstrument, DataSourceError
from nse_dashboard.infrastructure.brokers.base import BrokerAdapter


class UpstoxAdapter(BrokerAdapter):
    """Adapter for an authenticated Upstox History API compatible client."""

    name = "Upstox"

    def __init__(self, client: Any, instruments: dict[str, BrokerInstrument], **kwargs: Any) -> None:
        kwargs.setdefault("rate_limit_per_second", 10.0)
        super().__init__(client, instruments, **kwargs)

    def _fetch_candles(
        self, instrument: BrokerInstrument, start: datetime, end: datetime
    ) -> Sequence[Sequence[Any]]:
        method = getattr(self.client, "get_historical_candle_data1", None)
        if not callable(method):
            method = getattr(self.client, "get_historical_candle_data", None)
        if not callable(method):
            raise DataSourceError("Upstox client does not expose a historical candle method")
        response = method(
            instrument.token,
            "day",
            end.date().isoformat(),
            start.date().isoformat(),
            "2.0",
        )
        if isinstance(response, dict):
            data = response.get("data", response)
            candles = data.get("candles", []) if isinstance(data, dict) else []
            return [row[:6] for row in candles]
        data = getattr(response, "data", None)
        return [row[:6] for row in (getattr(data, "candles", None) or [])]
