from __future__ import annotations

import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Condition, Lock
from typing import Any, TypeVar

import pandas as pd

from nse_dashboard.domain.market_data import BrokerInstrument, DataSourceError

T = TypeVar("T")
_PERIOD = re.compile(r"^(?P<count>[1-9]\d*)(?P<unit>d|wk|mo|y)$")
_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("reconnect delays cannot be negative")
        if self.multiplier < 1:
            raise ValueError("reconnect multiplier must be at least one")


class RateLimiter:
    """Thread-safe token bucket shared by every request from an adapter."""

    def __init__(
        self,
        rate_per_second: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0 or burst < 1:
            raise ValueError("rate_per_second and burst must be positive")
        self.rate = float(rate_per_second)
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._updated = clock()
        self._clock = clock
        self._sleep = sleep
        self._condition = Condition(Lock())

    def acquire(self) -> None:
        while True:
            with self._condition:
                now = self._clock()
                elapsed = max(0.0, now - self._updated)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_for = (1 - self._tokens) / self.rate
            self._sleep(wait_for)


def period_start(period: str, now: datetime | None = None) -> datetime:
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    normalized_period = period.strip().lower()
    if normalized_period == "max":
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    match = _PERIOD.fullmatch(normalized_period)
    if not match:
        raise DataSourceError(f"Unsupported history period: {period}")
    count = int(match.group("count"))
    days = {"d": 1, "wk": 7, "mo": 31, "y": 366}[match.group("unit")]
    return end - timedelta(days=count * days)


def validated_candles(rows: Sequence[Sequence[Any]], provider: str, symbol: str) -> pd.DataFrame:
    """Return sorted, unique, canonical OHLCV candles or fail closed."""

    if not rows:
        raise DataSourceError(f"{provider} returned no data for {symbol}")
    try:
        frame = pd.DataFrame(rows, columns=("Timestamp", *_COLUMNS))
        frame["Timestamp"] = pd.to_datetime(frame["Timestamp"], utc=True, errors="coerce")
        for column in _COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["Timestamp", *_COLUMNS])
        finite = frame[list(_COLUMNS)].map(lambda value: math.isfinite(float(value))).all(axis=1)
        valid_range = (
            (frame["Volume"] >= 0)
            & (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
            & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
        )
        frame = frame[finite & valid_range]
        frame = frame.drop_duplicates(subset="Timestamp", keep="last").sort_values("Timestamp")
    except (AssertionError, TypeError, ValueError) as exc:
        raise DataSourceError(f"{provider} returned malformed data for {symbol}") from exc
    if frame.empty:
        raise DataSourceError(f"{provider} returned no valid data for {symbol}")
    return frame.set_index("Timestamp")[list(_COLUMNS)]


class BrokerAdapter(ABC):
    """Common resilience and normalization for synchronous broker SDK clients."""

    name: str
    retryable_exceptions: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)

    def __init__(
        self,
        client: Any,
        instruments: Mapping[str, BrokerInstrument],
        *,
        rate_limit_per_second: float,
        rate_limit_burst: int = 1,
        reconnect_policy: ReconnectPolicy | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.instruments = {key.strip().upper(): value for key, value in instruments.items()}
        self.limiter = limiter or RateLimiter(rate_limit_per_second, rate_limit_burst)
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()
        self._sleep = sleep
        self._reconnect_lock = Lock()

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        normalized = symbol.strip().upper()
        instrument = self.instruments.get(normalized)
        if instrument is None:
            raise DataSourceError(f"No {self.name} instrument mapping for {normalized}")
        end = datetime.now(timezone.utc)
        start = period_start(period, end)
        try:
            rows = self._with_reconnect(lambda: self._fetch_candles(instrument, start, end))
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(f"{self.name} history failed for {normalized}") from exc
        return validated_candles(rows, self.name, normalized)

    def market_history(self, symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
        return {symbol: self.history(symbol, period) for symbol in symbols}

    def _with_reconnect(self, operation: Callable[[], T]) -> T:
        delay = self.reconnect_policy.initial_delay_seconds
        for attempt in range(1, self.reconnect_policy.max_attempts + 1):
            self.limiter.acquire()
            try:
                return operation()
            except self.retryable_exceptions:
                if attempt == self.reconnect_policy.max_attempts:
                    raise
                self._sleep(delay)
                self._reconnect()
                delay = min(self.reconnect_policy.max_delay_seconds, delay * self.reconnect_policy.multiplier)
        raise AssertionError("unreachable")

    def _reconnect(self) -> None:
        # Serialize reconnects so a burst of failed worker threads does not create
        # multiple sessions. SDK wrappers may expose either method name.
        with self._reconnect_lock:
            reconnect = getattr(self.client, "reconnect", None) or getattr(self.client, "connect", None)
            if callable(reconnect):
                reconnect()

    @abstractmethod
    def _fetch_candles(
        self, instrument: BrokerInstrument, start: datetime, end: datetime
    ) -> Sequence[Sequence[Any]]: ...
