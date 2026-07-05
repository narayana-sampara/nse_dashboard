from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.options import OptionTick, OptionType
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.options.analytics import analyze_option_chain
from nse_dashboard.options.greeks import black_scholes_greeks
from nse_dashboard.options.max_pain import calculate_max_pain
from nse_dashboard.options.open_interest import open_interest_summary
from nse_dashboard.options.vwap import option_vwap
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeAdapter, FakeSnapshots


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(days=30)


def tick(symbol: str, strike: float, kind: str, **values) -> OptionTick:
    return OptionTick(
        symbol=symbol,
        underlying="NIFTY",
        expiry=EXPIRY,
        timestamp=NOW,
        strike=strike,
        option_type=kind,
        spot_price=100,
        option_price=values.pop("option_price", 5),
        implied_volatility=0.2,
        **values,
    )


def test_tick_normalizes_option_type_and_rejects_crossed_market() -> None:
    assert tick("nifty-ce", 100, "CE").option_type is OptionType.CALL
    with pytest.raises(ValueError, match="bid cannot exceed ask"):
        tick("NIFTY-CE", 100, "call", bid=6, ask=5)


def test_black_scholes_greeks_have_expected_signs() -> None:
    call = black_scholes_greeks(tick("C", 100, "call"), risk_free_rate=0)
    put = black_scholes_greeks(tick("P", 100, "put"), risk_free_rate=0)
    assert 0 < call.delta < 1
    assert -1 < put.delta < 0
    assert call.gamma == pytest.approx(put.gamma)
    assert call.vega == pytest.approx(put.vega)


def test_open_interest_vwap_and_max_pain() -> None:
    chain = [
        tick("90C", 90, "call", open_interest=10, volume=1, option_price=12),
        tick("100C", 100, "call", open_interest=20, volume=3, option_price=5),
        tick("100P", 100, "put", open_interest=15, volume=2, option_price=4),
        tick("110P", 110, "put", open_interest=15, volume=4, option_price=11),
    ]
    assert open_interest_summary(chain)["put_call_ratio"] == 1
    assert option_vwap(chain) == pytest.approx(7.9)
    assert calculate_max_pain(chain)["strike"] == 100


def test_combined_analytics_contains_all_phase_four_outputs() -> None:
    chain = [
        tick("100C", 100, "call", open_interest=100, previous_open_interest=50, volume=200),
        tick("100P", 100, "put", open_interest=200, previous_open_interest=190, volume=10),
    ]
    result = analyze_option_chain(chain)
    assert result["contracts"] == 2
    assert set(result) >= {"greeks", "open_interest", "max_pain", "gex", "vwap", "unusual_activity"}
    assert result["unusual_activity"][0]["symbol"] == "100C"


def test_chain_cannot_mix_expiries() -> None:
    other = tick("100P", 100, "put")
    object.__setattr__(other, "expiry", EXPIRY + timedelta(days=7))
    with pytest.raises(ValueError, match="same underlying and expiry"):
        analyze_option_chain([tick("100C", 100, "call"), other])


def test_options_analytics_endpoint_normalizes_json_ticks() -> None:
    service = SignalService(FakeAdapter(), MemoryTtlCache(), snapshots=FakeSnapshots())
    payload = [
        {
            "symbol": symbol,
            "underlying": "NIFTY",
            "expiry": EXPIRY.isoformat(),
            "timestamp": NOW.isoformat(),
            "strike": "100",
            "option_type": kind,
            "spot_price": "100",
            "option_price": "5",
            "open_interest": "100",
            "volume": "200",
            "implied_volatility": "0.2",
        }
        for symbol, kind in (("100C", "CE"), ("100P", "PE"))
    ]
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.post("/api/v1/options/analytics", json=payload)

    assert response.status_code == 200
    assert response.json()["contracts"] == 2
