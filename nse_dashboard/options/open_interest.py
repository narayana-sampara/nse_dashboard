from __future__ import annotations

from collections.abc import Iterable

from nse_dashboard.domain.options import OptionTick, OptionType


def open_interest_summary(ticks: Iterable[OptionTick]) -> dict[str, int | float | None]:
    chain = list(ticks)
    call_oi = sum(t.open_interest for t in chain if t.option_type is OptionType.CALL)
    put_oi = sum(t.open_interest for t in chain if t.option_type is OptionType.PUT)
    call_change = sum(t.open_interest_change or 0 for t in chain if t.option_type is OptionType.CALL)
    put_change = sum(t.open_interest_change or 0 for t in chain if t.option_type is OptionType.PUT)
    return {
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "put_call_ratio": put_oi / call_oi if call_oi else None,
        "call_open_interest_change": call_change,
        "put_open_interest_change": put_change,
        "change_put_call_ratio": put_change / call_change if call_change else None,
    }
