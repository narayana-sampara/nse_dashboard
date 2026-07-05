from __future__ import annotations

from collections.abc import Iterable

from nse_dashboard.domain.options import OptionTick, OptionType
from nse_dashboard.options.greeks import black_scholes_greeks


def gamma_exposure(
    ticks: Iterable[OptionTick],
    risk_free_rate: float = 0.07,
    dividend_yield: float = 0.0,
) -> dict[str, object]:
    """Calculate dealer GEX for a 1% underlying move (puts use negative sign)."""
    by_strike: dict[float, float] = {}
    by_symbol: dict[str, float] = {}
    for tick in ticks:
        gamma = black_scholes_greeks(tick, risk_free_rate, dividend_yield).gamma
        sign = 1.0 if tick.option_type is OptionType.CALL else -1.0
        exposure = sign * gamma * tick.open_interest * tick.lot_size * tick.spot_price**2 * 0.01
        by_symbol[tick.symbol] = exposure
        by_strike[tick.strike] = by_strike.get(tick.strike, 0.0) + exposure
    return {
        "total": sum(by_symbol.values()),
        "by_strike": {str(key): by_strike[key] for key in sorted(by_strike)},
        "by_symbol": by_symbol,
    }
