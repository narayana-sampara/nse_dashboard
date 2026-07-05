from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.options import OptionTick
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.options.smart_money import SMART_MONEY_WEIGHTS, rank_smart_money
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeAdapter, FakeSnapshots


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
EXPIRY = START + timedelta(days=90)


def history(symbol: str = "NIFTY-C") -> list[OptionTick]:
    ticks = []
    implied_volatility = 0.20
    for index in range(20):
        open_interest = 100 + index**2
        if index:
            implied_volatility *= 1 + index * 0.001
        spread = 2 - index * 0.095
        ticks.append(
            OptionTick(
                symbol=symbol,
                underlying="NIFTY",
                expiry=EXPIRY,
                timestamp=START + timedelta(days=index),
                strike=100,
                option_type="call",
                spot_price=100,
                option_price=10,
                open_interest=open_interest,
                previous_open_interest=100,
                volume=open_interest * (index + 1),
                implied_volatility=implied_volatility,
                bid=10 - spread / 2,
                ask=10 + spread / 2,
                lot_size=50,
            )
        )
    return ticks


def test_smart_money_formula_and_normalization() -> None:
    result = rank_smart_money(history())

    assert SMART_MONEY_WEIGHTS == {
        "volume_ratio": 0.30,
        "open_interest_change": 0.25,
        "iv_momentum": 0.20,
        "gex_contribution": 0.15,
        "bid_ask_tightness": 0.10,
    }
    assert result[0]["sub_scores"] == {
        "volume_ratio": 100.0,
        "open_interest_change": 100.0,
        "iv_momentum": 100.0,
        "gex_contribution": 50.0,
        "bid_ask_tightness": 100.0,
    }
    assert result[0]["smart_money_score"] == 92.5
    assert result[0]["rank"] == 1


def test_smart_money_requires_full_history_and_market_inputs() -> None:
    with pytest.raises(ValueError, match="At least 20 daily observations"):
        rank_smart_money(history()[:19])

    invalid = history()
    object.__setattr__(invalid[-1], "bid", None)
    with pytest.raises(ValueError, match="bid, and ask are required"):
        rank_smart_money(invalid)


def test_smart_money_endpoint_returns_ranked_score() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    payload = [
        {
            "symbol": item.symbol,
            "underlying": item.underlying,
            "expiry": item.expiry.isoformat(),
            "timestamp": item.timestamp.isoformat(),
            "strike": item.strike,
            "option_type": item.option_type.value,
            "spot_price": item.spot_price,
            "option_price": item.option_price,
            "open_interest": item.open_interest,
            "previous_open_interest": item.previous_open_interest,
            "volume": item.volume,
            "implied_volatility": item.implied_volatility,
            "bid": item.bid,
            "ask": item.ask,
            "lot_size": item.lot_size,
        }
        for item in history()
    ]
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.post("/api/v1/options/smart-money", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["weights"] == SMART_MONEY_WEIGHTS
    assert body["ranking"][0]["smart_money_score"] == 92.5
