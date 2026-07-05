from __future__ import annotations

from collections.abc import Iterable

from nse_dashboard.domain.options import OptionTick


def option_vwap(ticks: Iterable[OptionTick]) -> float | None:
    chain = list(ticks)
    volume = sum(tick.volume for tick in chain)
    if volume == 0:
        return None
    return sum(tick.option_price * tick.volume for tick in chain) / volume
