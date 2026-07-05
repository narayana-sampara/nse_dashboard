from __future__ import annotations

from collections.abc import Iterable

from nse_dashboard.domain.options import OptionTick


def detect_unusual_activity(
    ticks: Iterable[OptionTick],
    volume_oi_ratio: float = 1.0,
    oi_change_ratio: float = 0.25,
    minimum_volume: int = 100,
) -> list[dict[str, object]]:
    """Flag liquid contracts with exceptional turnover or OI growth."""
    if volume_oi_ratio <= 0 or oi_change_ratio <= 0 or minimum_volume < 0:
        raise ValueError("Activity thresholds must be positive")
    findings = []
    for tick in ticks:
        turnover_ratio = tick.volume / tick.open_interest if tick.open_interest else None
        change = tick.open_interest_change
        change_ratio = (
            change / tick.previous_open_interest
            if change is not None and tick.previous_open_interest
            else None
        )
        reasons = []
        if tick.volume >= minimum_volume and (
            tick.open_interest == 0 or (turnover_ratio is not None and turnover_ratio >= volume_oi_ratio)
        ):
            reasons.append("high_volume_to_open_interest")
        if change_ratio is not None and abs(change_ratio) >= oi_change_ratio:
            reasons.append("large_open_interest_change")
        if reasons:
            findings.append(
                {
                    "symbol": tick.symbol,
                    "strike": tick.strike,
                    "option_type": tick.option_type.value,
                    "volume": tick.volume,
                    "open_interest": tick.open_interest,
                    "volume_oi_ratio": turnover_ratio,
                    "open_interest_change": change,
                    "open_interest_change_ratio": change_ratio,
                    "reasons": reasons,
                }
            )
    return sorted(
        findings,
        key=lambda item: (item["volume"], abs(item["open_interest_change"] or 0)),
        reverse=True,
    )
