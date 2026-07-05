from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from nse_dashboard.domain.options import OptionTick
from nse_dashboard.options.greeks import black_scholes_greeks

SMART_MONEY_WEIGHTS = {
    "volume_ratio": 0.30,
    "open_interest_change": 0.25,
    "iv_momentum": 0.20,
    "gex_contribution": 0.15,
    "bid_ask_tightness": 0.10,
}


@dataclass(frozen=True, slots=True)
class _Factors:
    volume_ratio: float
    open_interest_change: float
    iv_momentum: float
    gex_contribution: float
    bid_ask_tightness: float


def rank_smart_money(
    ticks: Iterable[OptionTick],
    risk_free_rate: float = 0.07,
    dividend_yield: float = 0.0,
    lookback_days: int = 20,
) -> list[dict[str, object]]:
    """Rank the latest option contracts using normalized 20-day flow factors.

    Input is daily contract history, not a point-in-time chain. Each contract must
    have at least ``lookback_days`` daily observations with IV and bid/ask data.
    Every factor is min-max normalized against that contract's trailing range.
    """
    if lookback_days < 2:
        raise ValueError("lookback_days must be at least 2")
    history = list(ticks)
    if not history:
        raise ValueError("Option history is required")
    if len({tick.underlying for tick in history}) != 1:
        raise ValueError("All option history must have the same underlying")

    daily: dict[str, dict[date, OptionTick]] = defaultdict(dict)
    for tick in sorted(history, key=lambda item: item.timestamp):
        if tick.implied_volatility is None or tick.bid is None or tick.ask is None:
            raise ValueError(
                f"Implied volatility, bid, and ask are required for {tick.symbol}"
            )
        daily[tick.symbol][tick.timestamp.date()] = tick

    insufficient = sorted(
        symbol for symbol, observations in daily.items() if len(observations) < lookback_days
    )
    if insufficient:
        raise ValueError(
            f"At least {lookback_days} daily observations are required for: "
            + ", ".join(insufficient)
        )

    windows = {
        symbol: sorted(observations.items())[-lookback_days:]
        for symbol, observations in daily.items()
    }
    gex_by_day: dict[date, dict[str, float]] = defaultdict(dict)
    for symbol, observations in windows.items():
        for day, tick in observations:
            gamma = black_scholes_greeks(tick, risk_free_rate, dividend_yield).gamma
            gex_by_day[day][symbol] = abs(
                gamma * tick.open_interest * tick.lot_size * tick.spot_price**2 * 0.01
            )

    factors: dict[str, list[_Factors]] = {}
    for symbol, observations in windows.items():
        series: list[_Factors] = []
        previous: OptionTick | None = None
        for day, tick in observations:
            prior_oi = (
                tick.previous_open_interest
                if tick.previous_open_interest is not None
                else previous.open_interest if previous is not None else tick.open_interest
            )
            oi_change = (tick.open_interest - prior_oi) / max(prior_oi, 1)
            iv_momentum = (
                (tick.implied_volatility / previous.implied_volatility) - 1
                if previous is not None and previous.implied_volatility
                else 0.0
            )
            day_gex = gex_by_day[day]
            total_gex = sum(day_gex.values())
            series.append(
                _Factors(
                    volume_ratio=tick.volume / max(tick.open_interest, 1),
                    open_interest_change=oi_change,
                    iv_momentum=iv_momentum,
                    gex_contribution=day_gex[symbol] / total_gex if total_gex else 0.0,
                    bid_ask_tightness=_bid_ask_tightness(tick),
                )
            )
            previous = tick
        factors[symbol] = series

    ranked = []
    for symbol, series in factors.items():
        latest_tick = windows[symbol][-1][1]
        latest = series[-1]
        sub_scores = {
            name: _normalize(getattr(latest, name), [getattr(item, name) for item in series])
            for name in SMART_MONEY_WEIGHTS
        }
        score = sum(sub_scores[name] * weight for name, weight in SMART_MONEY_WEIGHTS.items())
        ranked.append(
            {
                "symbol": symbol,
                "underlying": latest_tick.underlying,
                "expiry": latest_tick.expiry.isoformat(),
                "timestamp": latest_tick.timestamp.isoformat(),
                "strike": latest_tick.strike,
                "option_type": latest_tick.option_type.value,
                "smart_money_score": round(score, 2),
                "sub_scores": {name: round(value, 2) for name, value in sub_scores.items()},
                "raw_factors": {
                    name: round(getattr(latest, name), 8) for name in SMART_MONEY_WEIGHTS
                },
                "lookback_days": lookback_days,
            }
        )
    ranked.sort(key=lambda item: (-float(item["smart_money_score"]), str(item["symbol"])))
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
    return ranked


def _normalize(value: float, history: list[float]) -> float:
    low, high = min(history), max(history)
    if high == low:
        return 50.0
    return max(0.0, min(100.0, (value - low) / (high - low) * 100.0))


def _bid_ask_tightness(tick: OptionTick) -> float:
    assert tick.bid is not None and tick.ask is not None
    midpoint = (tick.bid + tick.ask) / 2
    if midpoint <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (tick.ask - tick.bid) / midpoint))
