from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from nse_dashboard.domain.market_data import BrokerInstrument, DataSourceError
from nse_dashboard.infrastructure.brokers.base import BrokerAdapter


class ShoonyaAdapter(BrokerAdapter):
    """Adapter for a logged-in Noren/Shoonya compatible client."""

    name = "Shoonya"

    def __init__(self, client: Any, instruments: dict[str, BrokerInstrument], **kwargs: Any) -> None:
        kwargs.setdefault("rate_limit_per_second", 10.0)
        super().__init__(client, instruments, **kwargs)

    def _fetch_candles(
        self, instrument: BrokerInstrument, start: datetime, end: datetime
    ) -> Sequence[Sequence[Any]]:
        response = self.client.get_time_price_series(
            exchange=instrument.exchange,
            token=instrument.token,
            starttime=int(start.timestamp()),
            endtime=int(end.timestamp()),
            interval=1440,
        )
        if isinstance(response, dict):
            raise DataSourceError(f"Shoonya request failed: {response.get('emsg', 'invalid response')}")
        if not isinstance(response, list):
            raise DataSourceError("Shoonya returned an invalid response")
        def timestamp(row: dict[str, Any]) -> Any:
            epoch = row.get("ssboe")
            if epoch not in (None, ""):
                try:
                    return datetime.fromtimestamp(float(epoch), tz=start.tzinfo)
                except (TypeError, ValueError, OSError):
                    pass
            # Shoonya's documented fallback format is day-first.
            value = row.get("time")
            try:
                return datetime.strptime(value, "%d-%m-%Y %H:%M:%S").replace(tzinfo=start.tzinfo)
            except (TypeError, ValueError):
                return value
        return [
            [
                timestamp(row),
                row.get("into"),
                row.get("inth"),
                row.get("intl"),
                row.get("intc"),
                row.get("intv", 0),
            ]
            for row in response
        ]
