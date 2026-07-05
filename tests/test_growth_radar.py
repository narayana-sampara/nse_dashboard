from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.growth_radar import (
    GrowthRadarService,
    build_projections,
    candidate_state,
    evaluate_signal_outcomes,
    score_accumulation,
    score_growth_factors,
)
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeSnapshots


def growth_features() -> dict:
    return {
        "market_cap": 20_000_000_000,
        "listing_history_years": 8,
        "security_series": "EQ",
        "revenue_ttm": 10_000_000_000,
        "ebitda": 1_500_000_000,
        "ebitda_previous": 1_000_000_000,
        "ebitda_margin_pct": 15,
        "ebitda_margin_previous_pct": 11,
        "pat": 800_000_000,
        "pat_previous": 500_000_000,
        "eps_ttm": 8,
        "revenue_growth_qoq_pct": 12,
        "revenue_growth_previous_qoq_pct": 5,
        "revenue_growth_yoy_pct": 24,
        "revenue_growth_previous_yoy_pct": 12,
        "pat_growth_qoq_pct": 20,
        "pat_growth_previous_qoq_pct": 8,
        "eps_growth_yoy_pct": 30,
        "eps_growth_previous_yoy_pct": 15,
        "roce_pct": 22,
        "cash_conversion_pct": 85,
        "order_book_to_revenue": 2.6,
        "book_to_bill": 1.35,
        "new_order_growth_pct": 28,
        "order_execution_growth_pct": 20,
        "sector_capex_score": 80,
        "receivable_days": 95,
        "net_debt_to_ebitda": 0.8,
        "interest_coverage": 7,
        "operating_cash_flow": 900_000_000,
        "pe_to_sector": 0.8,
        "ev_ebitda_to_sector": 0.85,
        "price_sales_to_history": 0.9,
        "peg_ratio": 0.9,
        "institutional_holding_change_qoq_pct": 1.2,
        "promoter_holding_change_qoq_pct": 0,
        "promoter_pledge_pct": 0,
        "catalyst_score": 82,
        "net_debt": 1_000_000_000,
        "pe_ratio": 20,
        "ev_ebitda": 11,
        "price_sales": 2,
    }


def price_frame() -> pd.DataFrame:
    dates = pd.date_range("2023-01-02", periods=700, freq="B")
    trend = np.linspace(100, 250, len(dates))
    close = trend + np.sin(np.arange(len(dates)) / 10)
    volume = np.concatenate(
        [np.full(len(dates) - 20, 500_000), np.full(20, 900_000)]
    )
    return pd.DataFrame(
        {
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


def test_growth_factor_models_reward_operating_inflection() -> None:
    result = score_growth_factors(growth_features())

    assert result["earnings_inflection"] >= 65
    assert result["order_book_capex"] >= 70
    assert result["turnaround_deleveraging"] >= 65
    assert result["penalty"] == 0


def test_governance_and_cash_flow_penalties_are_capped() -> None:
    features = {
        **growth_features(),
        "promoter_pledge_pct": 60,
        "equity_dilution_12m_pct": 40,
        "legal_risk_quotient": 100,
        "operating_cash_flow": -1,
        "auditor_qualification": True,
    }

    result = score_growth_factors(features)

    assert result["penalty"] == 40
    assert candidate_state(80, 80, result["penalty"]) == "REJECTED"


def test_accumulation_uses_relative_strength_and_volume() -> None:
    stock = price_frame()
    benchmark = stock.copy()
    benchmark["Close"] = np.linspace(100, 155, len(benchmark))

    result = score_accumulation(stock, benchmark)

    assert result["score"] > 60
    assert result["features"]["relative_strength_12m_pct"] > 0
    assert result["features"]["volume_ratio"] > 1


def test_projection_outputs_bear_base_bull_for_every_year() -> None:
    result = build_projections(200, growth_features())

    assert result["available"] is True
    assert [row["fiscal_year"] for row in result["years"]] == list(range(2027, 2036))
    for row in result["years"]:
        assert row["bear"]["price"] < row["base"]["price"] < row["bull"]["price"]
        assert "year_growth_pct" in row["base"]


def test_projection_refuses_negative_ebitda() -> None:
    result = build_projections(200, {**growth_features(), "ebitda": -1})

    assert result["available"] is False
    assert result["years"] == []


def test_outcome_validation_records_precision_drawdown_and_lead_time() -> None:
    result = evaluate_signal_outcomes(
        [
            {
                "stock_return_12m_pct": 70,
                "benchmark_return_12m_pct": 20,
                "stock_return_24m_pct": 130,
                "benchmark_return_24m_pct": 35,
                "maximum_drawdown_pct": -18,
                "lead_time_days": 190,
            },
            {
                "stock_return_12m_pct": 10,
                "benchmark_return_12m_pct": 12,
                "stock_return_24m_pct": 40,
                "benchmark_return_24m_pct": 25,
                "maximum_drawdown_pct": -30,
                "lead_time_days": 250,
            },
        ]
    )

    assert result["compounder_12m"]["precision_pct"] == 50
    assert result["compounder_12m"]["false_positives"] == 1
    assert result["multibagger_24m"]["median_lead_time_days"] == 190


class GrowthSnapshots(FakeSnapshots):
    def __init__(self) -> None:
        super().__init__()
        self.payload = {
            "generated_at": "2026-06-25T00:00:00+00:00",
            "market_date": "2026-06-24",
            "universe_size": 500,
            "eligible_stocks": 1,
            "candidates": [
                {
                    "rank": 1,
                    "symbol": "TEST.NS",
                    "name": "TEST",
                    "sector": "Industrials",
                    "as_of": "2026-06-24",
                    "current_price": 200,
                    "signal_date": "2026-01-01",
                    "signal_price": 100,
                    "return_since_signal_pct": 100,
                    "strength_score": 78,
                    "confidence_pct": 85,
                    "state": "QUALIFIED",
                    "algorithm_scores": {"earnings_inflection": 80},
                    "risk_flags": [],
                    "track_eligibility": {
                        "compounder_12m": True,
                        "multibagger_24m": True,
                    },
                    "projections": build_projections(200, growth_features()),
                }
            ],
            "disclaimer": "Research only.",
        }

    def latest_growth_radar(self):
        return self.payload

    def save_growth_features(self, snapshot):
        self.growth_features = {snapshot["symbol"]: snapshot}
        return 1

    def latest_growth_features(self, symbols, known_at=None):
        return {
            symbol: value
            for symbol, value in getattr(self, "growth_features", {}).items()
            if symbol in symbols
            and (known_at is None or value["known_at"] <= known_at)
        }


class GrowthAdapter:
    name = "growth-fake"

    def history(self, symbol: str, period: str):
        del symbol, period
        return price_frame()

    def market_history(self, symbols: list[str], period: str):
        del period
        return {symbol: price_frame() for symbol in symbols}


def test_growth_radar_api_exposes_detail_and_projections() -> None:
    service = SignalService(
        GrowthAdapter(), MemoryTtlCache(), snapshots=GrowthSnapshots()
    )
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        listing = client.get("/api/v1/growth-radar")
        detail = client.get("/api/v1/growth-radar/TEST.NS")
        projection = client.get("/api/v1/growth-radar/TEST.NS/projections")

    assert listing.status_code == 200
    assert listing.json()["candidates"][0]["signal_price"] == 100
    assert detail.status_code == 200
    assert detail.json()["state"] == "QUALIFIED"
    assert projection.status_code == 200
    assert len(projection.json()["projections"]["years"]) == 9


def test_point_in_time_growth_feature_lookup_contract() -> None:
    snapshots = GrowthSnapshots()
    snapshots.save_growth_features(
        {
            "symbol": "TEST.NS",
            "as_of": "2025-12-31",
            "known_at": "2026-01-15T00:00:00+00:00",
            "features": growth_features(),
        }
    )

    result = snapshots.latest_growth_features(
        ["TEST.NS"], known_at="2026-01-16T00:00:00+00:00"
    )

    assert result["TEST.NS"]["known_at"] == "2026-01-15T00:00:00+00:00"
