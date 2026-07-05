from __future__ import annotations

from collections.abc import Iterable

from nse_dashboard.domain.options import OptionTick, OptionType


def calculate_max_pain(ticks: Iterable[OptionTick]) -> dict[str, object]:
    chain = list(ticks)
    if not chain:
        raise ValueError("An option chain is required")
    strikes = sorted({tick.strike for tick in chain})
    payouts: dict[float, float] = {}
    for settlement in strikes:
        payouts[settlement] = sum(
            (
                max(settlement - tick.strike, 0.0)
                if tick.option_type is OptionType.CALL
                else max(tick.strike - settlement, 0.0)
            )
            * tick.open_interest
            * tick.lot_size
            for tick in chain
        )
    strike = min(strikes, key=lambda candidate: (payouts[candidate], candidate))
    return {
        "strike": strike,
        "payout": payouts[strike],
        "payouts": {str(key): value for key, value in payouts.items()},
    }
