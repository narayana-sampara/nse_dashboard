from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from nse_dashboard.domain.market_data import BrokerInstrument, DataSourceError
from nse_dashboard.infrastructure.brokers.base import BrokerAdapter


class AngelOneAdapter(BrokerAdapter):
    """Adapter for a connected SmartAPI ``SmartConnect`` compatible client."""

    name = "Angel One"

    def __init__(self, client: Any, instruments: dict[str, BrokerInstrument], **kwargs: Any) -> None:
        kwargs.setdefault("rate_limit_per_second", 3.0)
        super().__init__(client, instruments, **kwargs)

    def _fetch_candles(
        self, instrument: BrokerInstrument, start: datetime, end: datetime
    ) -> Sequence[Sequence[Any]]:
        response = self.client.getCandleData(
            {
                "exchange": instrument.exchange,
                "symboltoken": instrument.token,
                "interval": "ONE_DAY",
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            }
        )
        if not isinstance(response, dict) or response.get("status") is False:
            message = response.get("message", "invalid response") if isinstance(response, dict) else "invalid response"
            raise DataSourceError(f"Angel One request failed: {message}")
        return response.get("data") or []
