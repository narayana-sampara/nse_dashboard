from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    symbol: str
    entry_value: float
    net_pnl: float
    sector: str = "Unknown"


def backtest_report(equity: pd.Series, trades: Iterable[ClosedTrade], periods_per_year: int = 252) -> dict[str, Any]:
    curve = pd.to_numeric(equity, errors="coerce").dropna()
    if len(curve) < 2 or (curve <= 0).any():
        raise ValueError("Backtest equity must contain at least two positive observations")
    returns = curve.pct_change().dropna()
    years = max(len(returns) / periods_per_year, 1 / periods_per_year)
    cagr = (curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1
    drawdown = curve / curve.cummax() - 1
    volatility = float(returns.std(ddof=1) * math.sqrt(periods_per_year)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(periods_per_year)) if returns.std(ddof=1) > 0 else 0.0
    downside = returns[returns < 0].std(ddof=1)
    sortino = float(returns.mean() / downside * math.sqrt(periods_per_year)) if pd.notna(downside) and downside > 0 else 0.0
    max_drawdown = abs(float(drawdown.min()))
    closed = list(trades)
    wins = [trade.net_pnl for trade in closed if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in closed if trade.net_pnl < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    sector_pnl: dict[str, float] = {}
    for trade in closed:
        sector_pnl[trade.sector] = sector_pnl.get(trade.sector, 0) + trade.net_pnl
    total_abs_sector = sum(abs(value) for value in sector_pnl.values()) or 1
    return {
        "cagr_pct": round(cagr * 100, 2), "annualized_volatility_pct": round(volatility * 100, 2),
        "maximum_drawdown_pct": round(max_drawdown * 100, 2), "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2), "calmar": round(cagr / max(max_drawdown, 1e-12), 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "expectancy": round(sum(trade.net_pnl for trade in closed) / len(closed), 2) if closed else 0,
        "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0,
        "average_win": round(float(np.mean(wins)), 2) if wins else 0,
        "average_loss": round(float(np.mean(losses)), 2) if losses else 0,
        "trade_count": len(closed),
        "largest_sector_pnl_concentration_pct": round(max((abs(value) for value in sector_pnl.values()), default=0) / total_abs_sector * 100, 2),
    }


def promotion_assessment(report: dict[str, Any], *, parameter_neighbors_profitable: bool) -> dict[str, Any]:
    gates = {
        "positive_expectancy": float(report["expectancy"]) > 0,
        "profit_factor_at_least_1_3": report["profit_factor"] is not None and float(report["profit_factor"]) >= 1.3,
        "sharpe_at_least_1": float(report["sharpe"]) >= 1,
        "maximum_drawdown_at_most_15pct": float(report["maximum_drawdown_pct"]) <= 15,
        "at_least_150_trades": int(report["trade_count"]) >= 150,
        "parameter_neighbors_profitable": parameter_neighbors_profitable,
    }
    return {"promoted": all(gates.values()), "gates": gates}
