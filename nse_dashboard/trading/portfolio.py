from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from math import floor
from typing import Any


@dataclass(slots=True)
class PaperPosition:
    symbol: str
    sector: str
    entry_date: date
    entry_price: float
    quantity: int
    initial_stop: float
    current_stop: float
    highest_close: float
    remaining_quantity: int | None = None
    partial_exit_done: bool = False
    realized_pnl: float = 0.0
    sessions_held: int = 0
    state: str = "OPEN"

    def __post_init__(self) -> None:
        if self.remaining_quantity is None:
            self.remaining_quantity = self.quantity

    @property
    def initial_risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop

    def evaluate_close(self, *, close: float, atr: float, supertrend: float, rsi: float,
                       below_supertrend_days: int, weekly_below_ema10: bool) -> dict[str, Any]:
        self.sessions_held += 1
        self.highest_close = max(self.highest_close, close)
        r_multiple = (close - self.entry_price) / max(self.initial_risk_per_share, 1e-9)
        if r_multiple >= 1:
            self.current_stop = max(self.current_stop, self.entry_price)
        self.current_stop = max(self.current_stop, supertrend, self.highest_close - 2.5 * atr)
        action, quantity, reason = "HOLD", 0, "position remains above exit thresholds"
        if close <= self.current_stop:
            action, quantity, reason = "EXIT", int(self.remaining_quantity or 0), "protective stop"
        elif below_supertrend_days >= 2:
            action, quantity, reason = "EXIT", int(self.remaining_quantity or 0), "two closes below SuperTrend"
        elif weekly_below_ema10:
            action, quantity, reason = "EXIT", int(self.remaining_quantity or 0), "weekly close below 10-week EMA"
        elif self.sessions_held >= 40 and r_multiple < 1:
            action, quantity, reason = "EXIT", int(self.remaining_quantity or 0), "40-session time stop"
        elif self.sessions_held >= 63 and close <= self.current_stop:
            action, quantity, reason = "EXIT", int(self.remaining_quantity or 0), "three-month maximum hold"
        elif rsi > 75 and not self.partial_exit_done:
            quantity = max(1, int(self.remaining_quantity or 0) // 2)
            action, quantity, reason = "PARTIAL_EXIT", quantity, "RSI above 75"
            self.partial_exit_done = True
            self.state = "PARTIAL_EXIT"
        return {"action": action, "quantity": quantity, "reason": reason, "r_multiple": round(r_multiple, 2), "trailing_stop": round(self.current_stop, 2)}


@dataclass(slots=True)
class PaperPortfolio:
    starting_equity: float = 1_000_000
    cash: float = 1_000_000
    high_water_mark: float = 1_000_000
    realized_pnl: float = 0.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    paused_until_month_review: bool = False

    def summary(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        prices = prices or {}
        market_value = sum((position.remaining_quantity or 0) * prices.get(symbol, position.entry_price) for symbol, position in self.positions.items())
        equity = self.cash + market_value
        self.high_water_mark = max(self.high_water_mark, equity)
        drawdown = max(0.0, (self.high_water_mark - equity) / max(self.high_water_mark, 1) * 100)
        if drawdown >= 10:
            self.paused_until_month_review = True
        return {
            "equity": round(equity, 2), "cash": round(self.cash, 2),
            "market_value": round(market_value, 2), "high_water_mark": round(self.high_water_mark, 2),
            "drawdown_pct": round(drawdown, 2), "open_positions": len(self.positions),
            "risk_multiplier": 0.5 if drawdown >= 6 else 1.0,
            "new_entries_allowed": drawdown < 8 and not self.paused_until_month_review,
            "liquidate": drawdown >= 10,
            "positions": [
                {**asdict(position), "entry_date": position.entry_date.isoformat()}
                for position in self.positions.values()
            ],
        }


def size_position(*, equity: float, entry: float, stop: float, risk_pct: float,
                  estimated_cost_bps: float = 25, maximum_position_pct: float = 10) -> dict[str, Any]:
    if equity <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        raise ValueError("equity and prices must be positive, and stop must be below entry")
    risk_per_share = entry - stop + entry * estimated_cost_bps / 10_000
    risk_budget = equity * risk_pct / 100
    by_risk = floor(risk_budget / risk_per_share)
    by_value = floor(equity * maximum_position_pct / 100 / entry)
    quantity = max(0, min(by_risk, by_value))
    return {
        "quantity": quantity, "position_value": round(quantity * entry, 2),
        "risk_budget": round(risk_budget, 2), "estimated_risk": round(quantity * risk_per_share, 2),
        "estimated_cost_bps": estimated_cost_bps,
    }
