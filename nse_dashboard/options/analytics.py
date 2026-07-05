from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from nse_dashboard.domain.options import OptionTick
from nse_dashboard.options.gex import gamma_exposure
from nse_dashboard.options.greeks import black_scholes_greeks
from nse_dashboard.options.max_pain import calculate_max_pain
from nse_dashboard.options.open_interest import open_interest_summary
from nse_dashboard.options.unusual_activity import detect_unusual_activity
from nse_dashboard.options.vwap import option_vwap


@dataclass(frozen=True, slots=True)
class OptionAnalytics:
    risk_free_rate: float = 0.07
    dividend_yield: float = 0.0

    def analyze(self, ticks: Iterable[OptionTick]) -> dict[str, Any]:
        chain = list(ticks)
        if not chain:
            raise ValueError("An option chain is required")
        underlying = {tick.underlying for tick in chain}
        expiries = {tick.expiry for tick in chain}
        if len(underlying) != 1 or len(expiries) != 1:
            raise ValueError("All ticks must have the same underlying and expiry")
        greeks = {
            tick.symbol: black_scholes_greeks(
                tick, self.risk_free_rate, self.dividend_yield
            ).as_dict()
            for tick in chain
        }
        return {
            "underlying": chain[0].underlying,
            "expiry": chain[0].expiry.isoformat(),
            "as_of": max(tick.timestamp for tick in chain).isoformat(),
            "contracts": len(chain),
            "greeks": greeks,
            "open_interest": open_interest_summary(chain),
            "max_pain": calculate_max_pain(chain),
            "gex": gamma_exposure(chain, self.risk_free_rate, self.dividend_yield),
            "vwap": option_vwap(chain),
            "unusual_activity": detect_unusual_activity(chain),
        }


def analyze_option_chain(
    ticks: Iterable[OptionTick],
    risk_free_rate: float = 0.07,
    dividend_yield: float = 0.0,
) -> dict[str, Any]:
    return OptionAnalytics(risk_free_rate, dividend_yield).analyze(ticks)
