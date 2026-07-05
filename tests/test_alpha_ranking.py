from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from nse_dashboard.api.app import create_app
from nse_dashboard.core.settings import Settings
from nse_dashboard.domain.alpha import AlphaFeatureSet, FactorInput, normalize_symbol
from nse_dashboard.infrastructure.cache import MemoryTtlCache
from nse_dashboard.services.alpha_ranking import AlphaRankingService
from nse_dashboard.services.fundamentals import FundamentalService
from nse_dashboard.services.legal_risk import LegalRiskService
from nse_dashboard.services.sentiment import SentimentService, news_decay
from nse_dashboard.services.signals import SignalService
from tests.test_signal_service import FakeSnapshots


def test_fundamental_score_applies_valuation_premium_penalty() -> None:
    service = FundamentalService()
    base = {
        "roe_pct": 22,
        "roce_pct": 25,
        "operating_margin_pct": 24,
        "net_margin_pct": 16,
        "ttm_revenue_growth_pct": 18,
        "ttm_net_profit_growth_pct": 20,
        "fcf_growth_pct": 24,
        "debt_to_equity": 0.3,
        "current_ratio": 1.8,
        "promoter_holding_change_qoq_pct": 0.2,
        "sector_pe_ratio": 20,
    }
    normal = service.score({**base, "pe_ratio": 24})
    expensive = service.score({**base, "pe_ratio": 40})

    assert normal["features"]["value_trap_penalty"] == 0
    assert expensive["features"]["value_trap_penalty"] > 0
    assert expensive["score"] < normal["score"]


def test_news_decay_starts_after_five_days() -> None:
    assert news_decay(5) == 1
    assert round(news_decay(7), 4) == 0.5


def test_legal_risk_distinguishes_board_meeting_from_penalty() -> None:
    result = LegalRiskService().score(
        [
            {
                "id": "board",
                "event_type": "BOARD_MEETING",
                "event_at": "2026-06-20T00:00:00+00:00",
            },
            {
                "id": "penalty",
                "event_type": "SEBI_PENALTY",
                "event_at": "2026-06-20T00:00:00+00:00",
            },
        ],
        as_of=datetime(2026, 6, 24, tzinfo=timezone.utc),
    )

    assert result["contributions"]["board"] == 0
    assert result["contributions"]["penalty"] == 35
    assert result["risk_flag"] == "Medium"


def test_sentiment_uses_relevance_quality_and_decay() -> None:
    as_of = datetime(2026, 6, 24, tzinfo=timezone.utc)
    result = SentimentService().aggregate(
        [
            {
                "id": "fresh",
                "published_at": "2026-06-24T00:00:00+00:00",
                "raw_sentiment": 0.8,
                "relevance": 1,
                "source_quality": 1,
            },
            {
                "id": "old",
                "published_at": "2026-06-10T00:00:00+00:00",
                "raw_sentiment": -1,
                "relevance": 1,
                "source_quality": 1,
            },
        ],
        as_of=as_of,
    )

    assert result["score"] > 80
    assert result["trend"] == "Bullish"


def test_combined_score_exposes_factor_and_feature_contributions() -> None:
    service = AlphaRankingService(FakeSnapshots())
    candidate = {
        "symbol": "TEST.NS",
        "name": "TEST",
        "sector": "Technology",
        "score": 80,
        "target_probability": 0.7,
        "features": {"momentum_6m": 12},
        "score_breakdown": {"momentum": 40, "trend": 40},
        "entry": {"proposed_stop": 95},
    }
    features = AlphaFeatureSet(
        fundamental=FactorInput(
            75, "FULL", {"roe_pct": 20}, {"roe": 10}
        ),
        sentiment=FactorInput(
            60, "FULL", {"composite_score": 0.2}, {"news": 5}
        ),
        legal=FactorInput(
            20, "FULL", {"has_sebi_penalty": False}, {"legal_events": 2}
        ),
        options=FactorInput(65, "FULL", {"pcr": 1.1}, {"smart_money": 8}),
    )

    result = service.score_candidate(candidate, features)

    assert result is not None
    assert 0 <= result["combined_score"] <= 100
    assert result["fundamental_grade"] == "B"
    assert result["legal_penalty"] == -2
    assert set(result["factor_contributions"]) == {
        "technical", "options", "fundamental", "sentiment"
    }
    assert result["top_reasons"]


def test_symbol_normalization_preserves_bse_suffix() -> None:
    assert normalize_symbol("reliance") == "RELIANCE.NS"
    assert normalize_symbol("500325.bo") == "500325.BO"


class AnalysisAdapter:
    name = "analysis-fake"

    def __init__(self) -> None:
        dates = pd.date_range("2023-01-02", periods=900, freq="B")
        close = np.linspace(100, 220, len(dates)) + np.sin(np.arange(len(dates)) / 8)
        self.frame = pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 2,
                "Low": close - 2,
                "Close": close,
                "Volume": np.full(len(dates), 2_000_000),
            },
            index=dates,
        )

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        del symbol, period
        return self.frame.copy()

    def market_history(self, symbols: list[str], period: str):
        del period
        return {symbol: self.frame.copy() for symbol in symbols}


class AnalysisSnapshots(FakeSnapshots):
    def latest_alpha_features(self, symbols: list[str]):
        return {
            symbol: {
                "fundamental": {
                    "score": 82,
                    "coverage": "FULL",
                    "features": {
                        "roe_pct": 22,
                        "roce_pct": 24,
                        "debt_to_equity": 0.2,
                        "pe_ratio": 24,
                        "sector_pe_ratio": 28,
                        "pb_ratio": 3.1,
                        "ttm_revenue_growth_pct": 16,
                        "qoq_profit_growth_pct": 12,
                    },
                    "contributions": {"roe": 12},
                },
                "sentiment": {
                    "score": 64,
                    "coverage": "FULL",
                    "features": {"composite_score": 0.28},
                    "contributions": {"news": 5},
                },
                "legal": {
                    "risk_quotient": 10,
                    "coverage": "FULL",
                    "risk_flag": "Low",
                    "features": {"has_sebi_penalty": False},
                    "contributions": {},
                },
                "options": {
                    "score": 68,
                    "coverage": "FULL",
                    "features": {"pcr": 1.2, "oi_change_pct": 8, "iv_skew": -0.04, "gex": 1250000},
                    "contributions": {"pcr": 7},
                },
            }
            for symbol in symbols
        }


def test_single_stock_analysis_api_supports_bse_and_returns_indication() -> None:
    snapshots = AnalysisSnapshots()
    service = SignalService(
        AnalysisAdapter(), MemoryTtlCache(), snapshots=snapshots
    )
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.get("/api/v1/analysis/500325.BO")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "500325.BO"
    assert payload["indication"]["signal"] in {
        "BUY", "SELL", "WATCH", "HOLD", "AVOID"
    }
    assert payload["alpha"]["fundamental_grade"] == "A"
    assert payload["entry"]["proposed_stop"] > 0


def test_deep_dive_stock_analysis_returns_explainable_factors_and_projection() -> None:
    snapshots = AnalysisSnapshots()
    service = SignalService(
        AnalysisAdapter(), MemoryTtlCache(), snapshots=snapshots
    )
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        response = client.get("/api/v1/analysis/stock/RELIANCE?horizon=15d")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "RELIANCE.NS"
    assert payload["overall_signal"] in {
        "STRONG_BUY",
        "BUY",
        "HOLD",
        "SELL",
        "STRONG_SELL",
    }
    assert 0 <= payload["overall_score"] <= 100
    assert payload["confidence_interval"] in {"High", "Medium", "Low"}
    factors = payload["factor_breakdown"]
    assert set(factors) == {
        "technical",
        "fundamental",
        "smart_money_options",
        "news_sentiment_legal",
    }
    assert "EMA_BULLISH_ALIGNMENT" in factors["technical"]["condition_flags"]
    assert factors["fundamental"]["valuation"]["pe_ratio"] == 24
    assert factors["smart_money_options"]["pcr"] == 1.2
    assert factors["news_sentiment_legal"]["legal_risk"] == "LOW"
    assert payload["projected_returns"]["sample_size"] <= 50
    assert "horizon_15d" in payload["projected_returns"]


def test_deep_dive_stock_analysis_uses_cache_until_force_refresh() -> None:
    cache = MemoryTtlCache()
    service = SignalService(
        AnalysisAdapter(), cache, snapshots=AnalysisSnapshots()
    )
    with TestClient(create_app(Settings(environment="test"), service)) as client:
        first = client.get("/api/v1/analysis/stock/RELIANCE")
        second = client.get("/api/v1/analysis/stock/RELIANCE")
        refreshed = client.get("/api/v1/analysis/stock/RELIANCE?force_refresh=true")

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert first.json()["generated_at"] == second.json()["generated_at"]
