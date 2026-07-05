from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, exp, log, pi, sqrt

from nse_dashboard.domain.options import OptionTick, OptionType


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * pi)


def black_scholes_greeks(
    tick: OptionTick,
    risk_free_rate: float = 0.07,
    dividend_yield: float = 0.0,
) -> OptionGreeks:
    """Return Black-Scholes Greeks; theta is per day and vega/rho per 1% move."""
    volatility = tick.implied_volatility
    if volatility is None:
        raise ValueError(f"Implied volatility is required for {tick.symbol}")
    if not -1 < risk_free_rate < 1 or not -1 < dividend_yield < 1:
        raise ValueError("Rates must be decimal values between -1 and 1")

    spot, strike, years = tick.spot_price, tick.strike, tick.years_to_expiry
    root_t = sqrt(years)
    d1 = (
        log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility**2) * years
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discounted_spot = spot * exp(-dividend_yield * years)
    discounted_strike = strike * exp(-risk_free_rate * years)

    gamma = exp(-dividend_yield * years) * _pdf(d1) / (spot * volatility * root_t)
    vega = discounted_spot * _pdf(d1) * root_t / 100.0
    common_theta = -(discounted_spot * _pdf(d1) * volatility) / (2.0 * root_t)
    if tick.option_type is OptionType.CALL:
        delta = exp(-dividend_yield * years) * _cdf(d1)
        theta = common_theta - risk_free_rate * discounted_strike * _cdf(d2)
        theta += dividend_yield * discounted_spot * _cdf(d1)
        rho = years * discounted_strike * _cdf(d2) / 100.0
    else:
        delta = exp(-dividend_yield * years) * (_cdf(d1) - 1.0)
        theta = common_theta + risk_free_rate * discounted_strike * _cdf(-d2)
        theta -= dividend_yield * discounted_spot * _cdf(-d1)
        rho = -years * discounted_strike * _cdf(-d2) / 100.0
    return OptionGreeks(delta, gamma, theta / 365.0, vega, rho)
