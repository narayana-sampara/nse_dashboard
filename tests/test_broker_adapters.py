from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nse_dashboard.domain.market_data import BrokerInstrument, DataSourceError
from nse_dashboard.infrastructure.brokers import AngelOneAdapter, ShoonyaAdapter, UpstoxAdapter
from nse_dashboard.infrastructure.brokers.base import RateLimiter, ReconnectPolicy, period_start

INSTRUMENTS = {"ABC.NS": BrokerInstrument("NSE", "123", "ABC-EQ")}


class NoLimit:
    def acquire(self) -> None:
        pass


def options(**extra):
    return {
        "limiter": NoLimit(),
        "reconnect_policy": ReconnectPolicy(max_attempts=3, initial_delay_seconds=0),
        "sleep": lambda _: None,
        **extra,
    }


def test_angel_validates_sorts_and_deduplicates_candles() -> None:
    class Client:
        def getCandleData(self, params):
            assert params["symboltoken"] == "123"
            return {
                "status": True,
                "data": [
                    ["2026-01-02T00:00:00+05:30", 10, 12, 9, 11, 100],
                    ["2026-01-01T00:00:00+05:30", 8, 10, 7, 9, 90],
                    ["2026-01-02T00:00:00+05:30", 10, 13, 9, 12, 110],
                ],
            }

    frame = AngelOneAdapter(Client(), INSTRUMENTS, **options()).history("abc.ns", "1mo")

    assert frame["Close"].tolist() == [9, 12]
    assert frame.index.is_monotonic_increasing
    assert str(frame.index.tz) == "UTC"


def test_invalid_candle_ranges_fail_closed() -> None:
    class Client:
        def getCandleData(self, params):
            return {"status": True, "data": [["2026-01-01", 10, 9, 8, 11, -1]]}

    with pytest.raises(DataSourceError, match="no valid data"):
        AngelOneAdapter(Client(), INSTRUMENTS, **options()).history("ABC.NS", "1mo")


def test_connection_failure_reconnects_with_exponential_backoff() -> None:
    sleeps = []

    class Client:
        calls = 0
        reconnects = 0

        def reconnect(self):
            self.reconnects += 1

        def getCandleData(self, params):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("socket closed")
            return {"status": True, "data": [["2026-01-01", 10, 11, 9, 10, 1]]}

    client = Client()
    adapter = AngelOneAdapter(
        client,
        INSTRUMENTS,
        limiter=NoLimit(),
        reconnect_policy=ReconnectPolicy(max_attempts=3, initial_delay_seconds=0.25),
        sleep=sleeps.append,
    )

    adapter.history("ABC.NS", "1mo")
    assert client.reconnects == 2
    assert sleeps == [0.25, 0.5]


def test_rate_limiter_waits_until_token_is_available() -> None:
    now = [0.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
    limiter.acquire()
    limiter.acquire()

    assert waits == [0.5]


def test_shoonya_response_is_normalized() -> None:
    class Client:
        def get_time_price_series(self, **kwargs):
            return [{"time": "01-01-2026 09:15:00", "into": "10", "inth": "12", "intl": "9", "intc": "11", "intv": "100"}]

    frame = ShoonyaAdapter(Client(), INSTRUMENTS, **options()).history("ABC.NS", "1mo")
    assert frame.iloc[0].to_dict() == {"Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100}


def test_upstox_object_response_is_normalized() -> None:
    class Data:
        candles = [["2026-01-01T00:00:00+05:30", 10, 12, 9, 11, 100, 0]]

    class Response:
        data = Data()

    class Client:
        def get_historical_candle_data1(self, *args):
            return Response()

    frame = UpstoxAdapter(Client(), INSTRUMENTS, **options()).history("ABC.NS", "1mo")
    assert frame["Close"].tolist() == [11]


def test_period_and_instrument_mapping_are_validated() -> None:
    assert period_start("1wk", datetime(2026, 1, 8, tzinfo=timezone.utc)).day == 1
    with pytest.raises(DataSourceError, match="Unsupported history period"):
        period_start("yesterday")
    with pytest.raises(DataSourceError, match="instrument mapping"):
        AngelOneAdapter(object(), INSTRUMENTS, **options()).history("MISSING.NS", "1mo")
