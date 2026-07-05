from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


class DataSourceError(RuntimeError):
    """A market-data provider failed or returned unusable data."""


@dataclass(frozen=True, slots=True)
class BrokerInstrument:
    """Provider identifier for one symbol.

    Broker APIs do not accept Yahoo-style symbols.  Keeping this mapping at the
    boundary prevents provider tokens from leaking into the signal service.
    """

    exchange: str
    token: str
    trading_symbol: str | None = None

    def __post_init__(self) -> None:
        if not self.exchange.strip() or not self.token.strip():
            raise ValueError("Broker instrument exchange and token are required")


class MarketDataAdapter(Protocol):
    """Provider-neutral contract consumed by the computation layer."""

    name: str

    def history(self, symbol: str, period: str) -> pd.DataFrame: ...

    def market_history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]: ...
