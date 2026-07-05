from __future__ import annotations

from dataclasses import dataclass

from nse_dashboard.five_percent_strategy.models import StockFeatureRow


@dataclass(slots=True)
class RiskLimits:
    max_capital_per_trade_pct: float = 20.0
    max_risk_per_trade_pct: float = 1.0
    daily_max_loss_pct: float = 2.0
    weekly_max_loss_pct: float = 5.0
    min_avg_volume: float = 0.0
    min_avg_turnover: float = 10_000_000.0
    max_volatility: float = 90.0

    def __post_init__(self) -> None:
        if self.max_capital_per_trade_pct >= 100:
            raise ValueError(
                "max_capital_per_trade_pct of 100% risks the entire account on one trade; "
                "choose a smaller allocation"
            )


def passes_liquidity_and_volatility_filters(
    row: StockFeatureRow, limits: RiskLimits
) -> tuple[bool, str | None]:
    if row.avg_volume_20d < limits.min_avg_volume:
        return False, "average volume below minimum liquidity filter"
    if row.avg_traded_value_20d < limits.min_avg_turnover:
        return False, "average turnover below minimum liquidity filter"
    if row.volatility > limits.max_volatility:
        return False, "volatility exceeds the maximum allowed for this strategy"
    return True, None


def position_size(
    capital: float,
    entry_price: float,
    stop_loss_price: float,
    limits: RiskLimits,
) -> dict[str, float]:
    """Size a position so that a stop-loss exit does not exceed max_risk_per_trade_pct."""

    if capital <= 0 or entry_price <= 0:
        raise ValueError("capital and entry_price must be positive")
    risk_per_share = max(entry_price - stop_loss_price, 1e-9)
    max_risk_amount = capital * (limits.max_risk_per_trade_pct / 100)
    max_capital_amount = capital * (limits.max_capital_per_trade_pct / 100)

    shares_by_risk = max_risk_amount / risk_per_share
    shares_by_capital = max_capital_amount / entry_price
    shares = max(0, math_floor(min(shares_by_risk, shares_by_capital)))
    allocated_capital = shares * entry_price
    return {
        "shares": shares,
        "allocated_capital": round(allocated_capital, 2),
        "max_loss_amount": round(shares * risk_per_share, 2),
    }


def math_floor(value: float) -> int:
    return int(value // 1)
