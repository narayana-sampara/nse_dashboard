from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"

    @classmethod
    def parse(cls, value: str | OptionType) -> OptionType:
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower()
        aliases = {"c": cls.CALL, "ce": cls.CALL, "p": cls.PUT, "pe": cls.PUT}
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unsupported option type: {value}") from exc


@dataclass(frozen=True, slots=True)
class OptionTick:
    """Provider-neutral snapshot of one option contract.

    Prices and volatility are expressed in currency units and decimal form
    respectively (20% volatility is ``0.20``). Timestamps are normalized to UTC.
    """

    symbol: str
    underlying: str
    expiry: datetime
    timestamp: datetime
    strike: float
    option_type: OptionType | str
    spot_price: float
    option_price: float
    open_interest: int = 0
    volume: int = 0
    previous_open_interest: int | None = None
    implied_volatility: float | None = None
    bid: float | None = None
    ask: float | None = None
    lot_size: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "underlying", self.underlying.strip().upper())
        object.__setattr__(self, "option_type", OptionType.parse(self.option_type))
        object.__setattr__(self, "expiry", _utc(self.expiry))
        object.__setattr__(self, "timestamp", _utc(self.timestamp))

        if not self.symbol or not self.underlying:
            raise ValueError("Option symbol and underlying are required")
        for name in ("strike", "spot_price", "option_price"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        for name in ("open_interest", "volume"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.previous_open_interest is not None and self.previous_open_interest < 0:
            raise ValueError("previous_open_interest cannot be negative")
        if self.implied_volatility is not None and (
            not isfinite(self.implied_volatility) or self.implied_volatility <= 0
        ):
            raise ValueError("implied_volatility must be finite and greater than zero")
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be greater than zero")
        if self.expiry <= self.timestamp:
            raise ValueError("expiry must be later than timestamp")

    @property
    def years_to_expiry(self) -> float:
        return (self.expiry - self.timestamp).total_seconds() / (365.0 * 24 * 60 * 60)

    @property
    def open_interest_change(self) -> int | None:
        if self.previous_open_interest is None:
            return None
        return self.open_interest - self.previous_open_interest


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
