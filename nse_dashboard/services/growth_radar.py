from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from nse_dashboard.core.json import json_ready
from nse_dashboard.domain.alpha import normalize_symbol
from sector_map import SECTOR_MAP, display_name


MIN_PRICE = 20.0
MIN_MARKET_CAP = 5_000_000_000.0
MIN_MEDIAN_TRADED_VALUE = 50_000_000.0
MODEL_NAME = "early_growth_radar"
MODEL_VERSION = "1.0.0"
FEATURE_SET_VERSION = "growth-features-v1"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bounded(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _linear(value: Any, poor: float, good: float, *, neutral: float = 0.0) -> float:
    number = _number(value)
    if number is None or good == poor:
        return neutral
    return _bounded((number - poor) / (good - poor) * 100)


def _improvement(current: Any, previous: Any, scale: float) -> float:
    left, right = _number(current), _number(previous)
    if left is None or right is None:
        return 0.0
    return _bounded(50 + (left - right) / scale * 50)


def score_growth_factors(features: dict[str, Any]) -> dict[str, Any]:
    """Score point-in-time company evidence using transparent zero-to-100 models."""
    earnings_parts = {
        "revenue_acceleration": (
            _improvement(
                features.get("revenue_growth_qoq_pct"),
                features.get("revenue_growth_previous_qoq_pct"),
                15,
            )
            + _improvement(
                features.get("revenue_growth_yoy_pct"),
                features.get("revenue_growth_previous_yoy_pct"),
                15,
            )
        )
        / 2,
        "profit_acceleration": (
            _improvement(
                features.get("pat_growth_qoq_pct"),
                features.get("pat_growth_previous_qoq_pct"),
                20,
            )
            + _improvement(
                features.get("eps_growth_yoy_pct"),
                features.get("eps_growth_previous_yoy_pct"),
                20,
            )
        )
        / 2,
        "margin_expansion": _improvement(
            features.get("ebitda_margin_pct"),
            features.get("ebitda_margin_previous_pct"),
            4,
        ),
        "return_quality": (
            _linear(features.get("roce_pct"), 5, 25)
            + _linear(features.get("cash_conversion_pct"), 20, 100)
        )
        / 2,
    }
    earnings = sum(earnings_parts.values()) / len(earnings_parts)

    order_parts = {
        "order_book_depth": _linear(features.get("order_book_to_revenue"), 0.5, 3.0),
        "book_to_bill": _linear(features.get("book_to_bill"), 0.7, 1.5),
        "new_order_growth": _linear(features.get("new_order_growth_pct"), -10, 35),
        "execution": _linear(features.get("order_execution_growth_pct"), -10, 25),
        "capex_exposure": _linear(features.get("sector_capex_score"), 20, 90),
    }
    order_book = sum(order_parts.values()) / len(order_parts)
    receivable_days = _number(features.get("receivable_days"))
    if receivable_days is not None and receivable_days > 150:
        order_book -= min(25, (receivable_days - 150) * 0.25)
    order_book = _bounded(order_book)

    turnaround_parts = {
        "ebitda_inflection": 100.0
        if (_number(features.get("ebitda")) or 0) > 0
        and (_number(features.get("ebitda_previous")) or 0) <= 0
        else _improvement(
            features.get("ebitda_margin_pct"),
            features.get("ebitda_margin_previous_pct"),
            5,
        ),
        "profit_inflection": 100.0
        if (_number(features.get("pat")) or 0) > 0
        and (_number(features.get("pat_previous")) or 0) <= 0
        else _linear(features.get("pat_growth_yoy_pct"), -20, 40),
        "deleveraging": _linear(features.get("net_debt_to_ebitda"), 4.0, 0.0),
        "interest_cover": _linear(features.get("interest_coverage"), 1.0, 6.0),
        "cash_flow": _linear(features.get("operating_cash_flow"), 0, max(1.0, _number(features.get("pat")) or 1.0)),
    }
    turnaround = sum(turnaround_parts.values()) / len(turnaround_parts)

    valuation_parts = {
        "pe_relative": _linear(features.get("pe_to_sector"), 1.5, 0.5),
        "ev_ebitda_relative": _linear(features.get("ev_ebitda_to_sector"), 1.5, 0.5),
        "sales_relative": _linear(features.get("price_sales_to_history"), 1.5, 0.6),
        "growth_value": _linear(features.get("peg_ratio"), 2.0, 0.7),
    }
    valuation_values = [
        value
        for name, value in valuation_parts.items()
        if features.get(
            {
                "pe_relative": "pe_to_sector",
                "ev_ebitda_relative": "ev_ebitda_to_sector",
                "sales_relative": "price_sales_to_history",
                "growth_value": "peg_ratio",
            }[name]
        )
        is not None
    ]
    valuation = sum(valuation_values) / len(valuation_values) if valuation_values else 35.0

    ownership_parts = {
        "institutional_change": _linear(
            features.get("institutional_holding_change_qoq_pct"), -1, 2
        ),
        "promoter_stability": _linear(
            features.get("promoter_holding_change_qoq_pct"), -2, 0.5
        ),
        "pledge": _linear(features.get("promoter_pledge_pct"), 25, 0),
    }
    ownership = sum(ownership_parts.values()) / len(ownership_parts)
    catalyst = _bounded(_number(features.get("catalyst_score")) or 0)

    penalties: dict[str, float] = {}
    pledge = _number(features.get("promoter_pledge_pct")) or 0
    if pledge > 10:
        penalties["promoter_pledge"] = min(15.0, (pledge - 10) * 0.6)
    dilution = _number(features.get("equity_dilution_12m_pct")) or 0
    if dilution > 5:
        penalties["equity_dilution"] = min(10.0, (dilution - 5) * 0.5)
    legal = _number(features.get("legal_risk_quotient")) or 0
    if legal > 20:
        penalties["legal_governance"] = min(15.0, legal * 0.15)
    if (_number(features.get("operating_cash_flow")) or 0) < 0:
        penalties["negative_operating_cash_flow"] = 10.0
    if bool(features.get("auditor_qualification")):
        penalties["auditor_qualification"] = 15.0
    penalty = min(40.0, sum(penalties.values()))

    return {
        "earnings_inflection": round(earnings, 2),
        "order_book_capex": round(order_book, 2),
        "turnaround_deleveraging": round(turnaround, 2),
        "valuation": round(valuation, 2),
        "ownership": round(ownership, 2),
        "catalyst": round(catalyst, 2),
        "penalty": round(penalty, 2),
        "penalties": {key: round(value, 2) for key, value in penalties.items()},
        "components": {
            "earnings_inflection": {key: round(value, 2) for key, value in earnings_parts.items()},
            "order_book_capex": {key: round(value, 2) for key, value in order_parts.items()},
            "turnaround_deleveraging": {
                key: round(value, 2) for key, value in turnaround_parts.items()
            },
            "valuation": {key: round(value, 2) for key, value in valuation_parts.items()},
            "ownership": {key: round(value, 2) for key, value in ownership_parts.items()},
        },
    }


def score_accumulation(frame: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, Any]:
    data = frame.dropna(subset=["Close", "Volume"]).copy()
    index = benchmark.dropna(subset=["Close"]).copy()
    if len(data) < 260 or len(index) < 260:
        raise ValueError("Accumulation model needs at least 260 sessions")
    close = data["Close"].astype(float)
    volume = data["Volume"].astype(float)
    benchmark_close = index["Close"].astype(float).reindex(data.index).ffill()
    weekly = close.resample("W-FRI").last().dropna()
    ema30 = weekly.ewm(span=30, adjust=False).mean()
    stock_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100
    stock_12m = (close.iloc[-1] / close.iloc[-252] - 1) * 100
    benchmark_6m = (benchmark_close.iloc[-1] / benchmark_close.iloc[-126] - 1) * 100
    benchmark_12m = (benchmark_close.iloc[-1] / benchmark_close.iloc[-252] - 1) * 100
    relative_6m = stock_6m - benchmark_6m
    relative_12m = stock_12m - benchmark_12m
    high_52w = close.tail(252).max()
    proximity = close.iloc[-1] / max(high_52w, 1e-9) * 100
    volume_ratio = volume.tail(20).median() / max(volume.tail(120).median(), 1)
    recent_vol = close.pct_change().tail(20).std()
    prior_vol = close.pct_change().tail(120).head(100).std()
    contraction = prior_vol / max(recent_vol, 1e-9)
    components = {
        "relative_strength_6m": _linear(relative_6m, -10, 30),
        "relative_strength_12m": _linear(relative_12m, -15, 50),
        "rising_30_week_average": 100.0
        if len(ema30) >= 2 and weekly.iloc[-1] > ema30.iloc[-1] > ema30.iloc[-2]
        else 0.0,
        "near_52_week_high": _linear(proximity, 65, 100),
        "volume_accumulation": _linear(volume_ratio, 0.7, 1.8),
        "volatility_contraction": _linear(contraction, 0.7, 1.8),
    }
    score = sum(components.values()) / len(components)
    return {
        "score": round(score, 2),
        "components": {key: round(value, 2) for key, value in components.items()},
        "features": {
            "relative_strength_6m_pct": round(relative_6m, 2),
            "relative_strength_12m_pct": round(relative_12m, 2),
            "distance_from_52w_high_pct": round((close.iloc[-1] / high_52w - 1) * 100, 2),
            "volume_ratio": round(volume_ratio, 2),
            "volatility_contraction": round(contraction, 2),
        },
    }


def candidate_state(score: float, accumulation: float, penalty: float) -> str:
    if penalty >= 30 or score < 35:
        return "REJECTED"
    if score >= 78 and accumulation >= 75:
        return "BREAKOUT_CONFIRMED"
    if score >= 68:
        return "QUALIFIED"
    if score >= 55:
        return "BUILDING_STRENGTH"
    return "EARLY_WATCH"


def evaluate_signal_outcomes(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize completed point-in-time signal outcomes without look-ahead data."""
    tracks = {
        "compounder_12m": {
            "return_key": "stock_return_12m_pct",
            "benchmark_key": "benchmark_return_12m_pct",
            "minimum_return": 50.0,
            "minimum_excess": 30.0,
        },
        "multibagger_24m": {
            "return_key": "stock_return_24m_pct",
            "benchmark_key": "benchmark_return_24m_pct",
            "minimum_return": 100.0,
            "minimum_excess": 50.0,
        },
    }
    result: dict[str, Any] = {}
    for track, rule in tracks.items():
        completed = [
            record
            for record in records
            if _number(record.get(rule["return_key"])) is not None
            and _number(record.get(rule["benchmark_key"])) is not None
        ]
        hits = [
            record
            for record in completed
            if float(record[rule["return_key"]]) >= rule["minimum_return"]
            and float(record[rule["return_key"]]) - float(record[rule["benchmark_key"]])
            >= rule["minimum_excess"]
        ]
        drawdowns = [
            abs(float(record["maximum_drawdown_pct"]))
            for record in completed
            if _number(record.get("maximum_drawdown_pct")) is not None
        ]
        lead_times = sorted(
            float(record["lead_time_days"])
            for record in hits
            if _number(record.get("lead_time_days")) is not None
        )
        median_lead = None
        if lead_times:
            middle = len(lead_times) // 2
            median_lead = (
                lead_times[middle]
                if len(lead_times) % 2
                else (lead_times[middle - 1] + lead_times[middle]) / 2
            )
        precision = len(hits) / len(completed) * 100 if completed else 0.0
        median_drawdown = None
        if drawdowns:
            ordered_drawdowns = sorted(drawdowns)
            middle = len(ordered_drawdowns) // 2
            median_drawdown = (
                ordered_drawdowns[middle]
                if len(ordered_drawdowns) % 2
                else (ordered_drawdowns[middle - 1] + ordered_drawdowns[middle]) / 2
            )
        result[track] = {
            "completed_signals": len(completed),
            "successful_signals": len(hits),
            "false_positives": len(completed) - len(hits),
            "precision_pct": round(precision, 2),
            "false_positive_rate_pct": round(100 - precision, 2) if completed else 0.0,
            "median_lead_time_days": round(median_lead, 2) if median_lead is not None else None,
            "median_maximum_drawdown_pct": (
                round(median_drawdown, 2) if median_drawdown is not None else None
            ),
        }
    return result


def build_projections(
    current_price: float,
    features: dict[str, Any],
    *,
    start_year: int = 2027,
    end_year: int = 2035,
) -> dict[str, Any]:
    ebitda = _number(features.get("ebitda"))
    revenue = _number(features.get("revenue_ttm"))
    margin = _number(features.get("ebitda_margin_pct"))
    market_cap = _number(features.get("market_cap"))
    if ebitda is None or ebitda <= 0 or revenue is None or revenue <= 0 or not margin or not market_cap:
        return {
            "available": False,
            "reason": "Positive EBITDA, revenue, margin and market-cap coverage are required.",
            "years": [],
        }

    shares = market_cap / max(current_price, 1e-9)
    net_debt = _number(features.get("net_debt")) or 0.0
    eps = _number(features.get("eps_ttm"))
    base_growth = _number(features.get("revenue_growth_yoy_pct"))
    if base_growth is None:
        base_growth = _number(features.get("ttm_revenue_growth_pct")) or 10.0
    base_growth = max(6.0, min(30.0, base_growth))
    base_margin = max(1.0, margin)
    pe = max(5.0, min(45.0, _number(features.get("pe_ratio")) or 18.0))
    ev_ebitda = max(4.0, min(30.0, _number(features.get("ev_ebitda")) or 12.0))
    price_sales = max(0.3, min(8.0, _number(features.get("price_sales")) or market_cap / revenue))
    current_year = start_year - 1

    scenario_specs = {
        "bear": {"growth_delta": -7.0, "margin_delta": -2.0, "multiple": 0.78},
        "base": {"growth_delta": 0.0, "margin_delta": 1.0, "multiple": 1.0},
        "bull": {"growth_delta": 7.0, "margin_delta": 3.0, "multiple": 1.22},
    }
    paths: dict[str, list[dict[str, Any]]] = {}
    for scenario, spec in scenario_specs.items():
        scenario_revenue = revenue
        scenario_eps = eps
        previous_price = current_price
        rows = []
        for year in range(start_year, end_year + 1):
            step = year - current_year
            faded_growth = 6.0 + (base_growth + spec["growth_delta"] - 6.0) * (0.78 ** (step - 1))
            scenario_revenue *= 1 + faded_growth / 100
            scenario_margin = max(
                1.0,
                base_margin
                + spec["margin_delta"] * min(1.0, step / 4)
                - max(0, step - 5) * 0.15,
            )
            scenario_ebitda = scenario_revenue * scenario_margin / 100
            pe_value = None
            if scenario_eps is not None and scenario_eps > 0:
                eps_growth = max(4.0, faded_growth + (scenario_margin - base_margin) * 0.8)
                scenario_eps *= 1 + eps_growth / 100
                target_pe = 16 + (pe - 16) * (0.82 ** step)
                pe_value = scenario_eps * target_pe * spec["multiple"]
            target_ev_multiple = 10 + (ev_ebitda - 10) * (0.82 ** step)
            ev_value = max(0.0, scenario_ebitda * target_ev_multiple * spec["multiple"] - net_debt) / shares
            target_ps = 1.5 + (price_sales - 1.5) * (0.82 ** step)
            ps_value = scenario_revenue * target_ps * spec["multiple"] / shares
            values = [ev_value, ps_value] + ([pe_value] if pe_value is not None else [])
            price = sum(values) / len(values)
            rows.append(
                {
                    "fiscal_year": year,
                    "price": round(price, 2),
                    "year_growth_pct": round((price / previous_price - 1) * 100, 2),
                    "cumulative_growth_pct": round((price / current_price - 1) * 100, 2),
                    "revenue": round(scenario_revenue, 2),
                    "ebitda_margin_pct": round(scenario_margin, 2),
                    "eps": round(scenario_eps, 2) if scenario_eps is not None else None,
                    "valuation_multiple": round(target_ev_multiple * spec["multiple"], 2),
                }
            )
            previous_price = price
        paths[scenario] = rows

    years = []
    for index, year in enumerate(range(start_year, end_year + 1)):
        years.append(
            {
                "fiscal_year": year,
                "bear": paths["bear"][index],
                "base": paths["base"][index],
                "bull": paths["bull"][index],
            }
        )
    horizon = max(1, end_year - current_year)
    return {
        "available": True,
        "valuation_methods": ["P/E when EPS is positive", "EV/EBITDA", "Price/Sales"],
        "assumptions": {
            "starting_revenue_growth_pct": round(base_growth, 2),
            "long_term_growth_floor_pct": 6.0,
            "starting_ebitda_margin_pct": round(base_margin, 2),
            "net_debt": round(net_debt, 2),
        },
        "implied_cagr_pct": {
            scenario: round((rows[-1]["price"] / current_price) ** (1 / horizon) * 100 - 100, 2)
            for scenario, rows in paths.items()
        },
        "years": years,
    }


class GrowthRadarService:
    def __init__(self, adapter: Any, snapshots: Any, period: str = "max") -> None:
        self.adapter = adapter
        self.snapshots = snapshots
        self.period = period

    def score_candidate(
        self,
        symbol: str,
        sector: str,
        frame: pd.DataFrame,
        benchmark: pd.DataFrame,
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        features = dict(snapshot.get("features", snapshot))
        close = frame.dropna(subset=["Close", "Volume"])
        if len(close) < 260:
            return None
        price = float(close["Close"].iloc[-1])
        median_traded_value = float(
            (close["Close"].astype(float) * close["Volume"].astype(float)).tail(20).median()
        )
        market_cap = _number(features.get("market_cap"))
        listing_years = _number(features.get("listing_history_years"))
        exclusion_reasons = []
        if price < MIN_PRICE:
            exclusion_reasons.append("price_below_20")
        if market_cap is not None and market_cap < MIN_MARKET_CAP:
            exclusion_reasons.append("market_cap_below_500_crore")
        if median_traded_value < MIN_MEDIAN_TRADED_VALUE:
            exclusion_reasons.append("median_traded_value_below_5_crore")
        if listing_years is not None and listing_years < 3:
            exclusion_reasons.append("listing_history_below_3_years")
        if features.get("security_series") not in (None, "EQ"):
            exclusion_reasons.append("not_main_board_eq")
        if any(bool(features.get(flag)) for flag in ("suspended", "severe_surveillance", "sme")):
            exclusion_reasons.append("exchange_risk_filter")
        if exclusion_reasons:
            return None

        factors = score_growth_factors(features)
        accumulation = score_accumulation(close, benchmark)
        archetypes = sorted(
            [
                factors["earnings_inflection"],
                factors["order_book_capex"],
                factors["turnaround_deleveraging"],
            ],
            reverse=True,
        )
        contributions = {
            "best_archetype": archetypes[0] * 0.30,
            "second_archetype": archetypes[1] * 0.20,
            "accumulation": accumulation["score"] * 0.20,
            "valuation": factors["valuation"] * 0.10,
            "ownership": factors["ownership"] * 0.10,
            "catalyst": factors["catalyst"] * 0.10,
        }
        score = _bounded(sum(contributions.values()) - factors["penalty"])
        state = candidate_state(score, accumulation["score"], factors["penalty"])
        as_of = pd.to_datetime(close.index[-1]).date().isoformat()
        known_at = snapshot.get("known_at") or snapshot.get("as_of")
        evidence = list(snapshot.get("evidence", []))
        coverage_fields = [
            "market_cap", "revenue_ttm", "ebitda", "ebitda_margin_pct",
            "revenue_growth_yoy_pct", "order_book_to_revenue", "net_debt_to_ebitda",
            "promoter_pledge_pct",
        ]
        coverage = sum(features.get(key) is not None for key in coverage_fields) / len(coverage_fields)
        projections = build_projections(price, features)
        confidence = _bounded(coverage * 75 + min(len(evidence), 5) * 5)
        return json_ready(
            {
                "symbol": symbol,
                "name": display_name(symbol),
                "sector": sector,
                "as_of": as_of,
                "known_at": known_at,
                "current_price": round(price, 2),
                "median_traded_value": round(median_traded_value, 2),
                "market_cap": market_cap,
                "strength_score": round(score, 2),
                "state": state,
                "confidence_pct": round(confidence, 2),
                "algorithm_scores": {
                    "earnings_inflection": factors["earnings_inflection"],
                    "order_book_capex": factors["order_book_capex"],
                    "turnaround_deleveraging": factors["turnaround_deleveraging"],
                    "price_volume_accumulation": accumulation["score"],
                    "valuation": factors["valuation"],
                    "ownership": factors["ownership"],
                    "catalyst": factors["catalyst"],
                },
                "score_contributions": {
                    key: round(value, 2) for key, value in contributions.items()
                },
                "penalty": factors["penalty"],
                "risk_flags": list(factors["penalties"]),
                "factor_details": {
                    **factors["components"],
                    "price_volume_accumulation": accumulation,
                },
                "evidence": evidence,
                "data_freshness": snapshot.get("freshness_status", "UNKNOWN"),
                "projections": projections,
                "track_eligibility": {
                    "compounder_12m": score >= 68,
                    "multibagger_24m": score >= 60 and factors["penalty"] < 20,
                },
            }
        )

    def build(
        self,
        histories: dict[str, pd.DataFrame],
        feature_snapshots: dict[str, dict[str, Any]],
        *,
        benchmark_symbol: str = "^CRSLDX",
    ) -> dict[str, Any]:
        benchmark = histories.get(benchmark_symbol)
        if benchmark is None or benchmark.empty:
            raise ValueError("Nifty 500 benchmark history is required")
        candidates = []
        failures = []
        for symbol, sector in SECTOR_MAP.items():
            snapshot = feature_snapshots.get(symbol)
            frame = histories.get(symbol)
            if not snapshot or frame is None or frame.empty:
                continue
            try:
                item = self.score_candidate(symbol, sector, frame, benchmark, snapshot)
                if item is not None:
                    candidates.append(item)
            except (KeyError, TypeError, ValueError):
                failures.append(symbol)
        candidates.sort(
            key=lambda item: (
                -float(item["strength_score"]),
                -float(item["confidence_pct"]),
                str(item["symbol"]),
            )
        )
        for rank, item in enumerate(candidates, start=1):
            item["rank"] = rank
            first_signal = getattr(self.snapshots, "first_growth_signal", lambda _: None)(
                item["symbol"]
            )
            item["signal_date"] = (
                first_signal.get("signal_date") if first_signal else item["as_of"]
            )
            item["signal_price"] = (
                float(first_signal.get("signal_price"))
                if first_signal and first_signal.get("signal_price") is not None
                else item["current_price"]
            )
            item["return_since_signal_pct"] = round(
                (item["current_price"] / max(item["signal_price"], 1e-9) - 1) * 100,
                2,
            )
        result = {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_date": max(
                (item["as_of"] for item in candidates), default=None
            ),
            "model": {
                "name": MODEL_NAME,
                "version": MODEL_VERSION,
                "feature_set_version": FEATURE_SET_VERSION,
            },
            "universe_size": len(SECTOR_MAP),
            "eligible_stocks": len(candidates),
            "failures": failures,
            "filters": {
                "minimum_price": MIN_PRICE,
                "minimum_market_cap": MIN_MARKET_CAP,
                "minimum_median_traded_value": MIN_MEDIAN_TRADED_VALUE,
                "minimum_listing_history_years": 3,
            },
            "validation_targets": {
                "compounder_12m": {
                    "minimum_total_return_pct": 50,
                    "minimum_excess_return_pct": 30,
                },
                "multibagger_24m": {
                    "minimum_total_return_pct": 100,
                    "minimum_excess_return_pct": 50,
                },
            },
            "candidates": candidates,
            "disclaimer": (
                "Scenario prices are quantitative research estimates, not assured "
                "targets or investment advice."
            ),
        }
        return json_ready(result)

    def generate(self) -> dict[str, Any]:
        benchmark_symbol = "^CRSLDX"
        symbols = list(SECTOR_MAP)
        histories = self.adapter.market_history([*symbols, benchmark_symbol], self.period)
        loader = getattr(self.snapshots, "latest_growth_features", None)
        feature_snapshots = loader(symbols) if loader else {}
        result = self.build(histories, feature_snapshots, benchmark_symbol=benchmark_symbol)
        saver = getattr(self.snapshots, "save_growth_radar", None)
        if saver:
            saver(result)
        return result

    def latest(
        self,
        *,
        sector: str | None = None,
        state: str | None = None,
        algorithm: str | None = None,
        track: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        loader = getattr(self.snapshots, "latest_growth_radar", None)
        payload = loader() if loader else None
        if not payload:
            payload = {
                "generated_at": None,
                "market_date": None,
                "model": None,
                "universe_size": len(SECTOR_MAP),
                "eligible_stocks": 0,
                "candidates": [],
                "disclaimer": "No growth-radar run has been persisted.",
            }
        result = dict(payload)
        candidates = list(result.get("candidates", []))
        if sector:
            candidates = [item for item in candidates if item.get("sector") == sector]
        if state:
            candidates = [item for item in candidates if item.get("state") == state]
        if algorithm:
            candidates.sort(
                key=lambda item: -float(item.get("algorithm_scores", {}).get(algorithm, 0))
            )
        if track:
            candidates = [
                item
                for item in candidates
                if item.get("track_eligibility", {}).get(track, False)
            ]
        result["candidates"] = candidates[:limit]
        result["eligible_stocks"] = len(result["candidates"])
        return json_ready(result)

    def detail(self, symbol: str) -> dict[str, Any] | None:
        normalized = normalize_symbol(symbol)
        payload = self.latest(limit=1000)
        return next(
            (item for item in payload.get("candidates", []) if item["symbol"] == normalized),
            None,
        )
