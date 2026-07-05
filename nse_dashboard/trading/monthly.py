from __future__ import annotations

import math
from typing import Any

import pandas as pd

from nse_dashboard.trading.indicators import entry_indicators, market_regime, normalized_ohlcv
from nse_dashboard.trading.portfolio import size_position
from sector_map import display_name


NIFTY_50_SYMBOLS = frozenset({
    "ADANIPORTS.NS", "ASIANPAINT.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "BHARTIARTL.NS", "BPCL.NS",
    "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS",
    "DRREDDY.NS", "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS",
    "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS",
    "HINDUNILVR.NS", "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS",
    "ITC.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS",
    "MARUTI.NS", "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS",
    "RELIANCE.NS", "SBILIFE.NS", "SBIN.NS", "SUNPHARMA.NS",
    "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS",
    "TECHM.NS", "TITAN.NS", "ULTRACEMCO.NS", "UPL.NS", "WIPRO.NS",
})


class ConservativeMonthlyStrategy:
    name = "conservative_nse_monthly"
    version = "2.0.0"
    minimum_rows = 300

    def regime(self, benchmark: pd.DataFrame) -> dict[str, Any]:
        return market_regime(benchmark)

    def evaluate(
        self,
        symbol: str,
        sector: str,
        frame: pd.DataFrame,
        benchmark: pd.DataFrame,
        *,
        portfolio_equity: float = 1_000_000,
        estimated_cost_bps: float = 25,
    ) -> dict[str, Any]:
        data = normalized_ohlcv(frame, self.minimum_rows)
        index = normalized_ohlcv(benchmark, self.minimum_rows)
        if data.index[-1].date() != index.index[-1].date():
            raise ValueError("Stock and benchmark data must share the latest completed session")
        close = data["Close"]
        benchmark_close = index["Close"].reindex(data.index).ffill()
        monthly = close.resample("ME").last().dropna()
        benchmark_monthly = benchmark_close.resample("ME").last().dropna()
        weekly = close.resample("W-FRI").last().dropna()
        latest = data.index[-1].normalize()
        if latest < pd.offsets.BMonthEnd().rollforward(latest).normalize():
            monthly = monthly.iloc[:-1]
            benchmark_monthly = benchmark_monthly.iloc[:-1]
        if latest.weekday() < 4:
            weekly = weekly.iloc[:-1]
        if len(monthly) < 14 or len(weekly) < 31:
            raise ValueError("Monthly selection needs 14 monthly and 31 weekly completed bars")

        ema10m = monthly.ewm(span=10, adjust=False).mean()
        ema30w = weekly.ewm(span=30, adjust=False).mean()
        momentum_6m = float((monthly.iloc[-1] / monthly.iloc[-7] - 1) * 100)
        momentum_12_1 = float((monthly.iloc[-2] / monthly.iloc[-13] - 1) * 100)
        benchmark_6m = float((benchmark_monthly.iloc[-1] / benchmark_monthly.iloc[-7] - 1) * 100)
        relative_strength = momentum_6m - benchmark_6m
        monthly_spread = float((monthly.iloc[-1] / ema10m.iloc[-1] - 1) * 100)
        weekly_spread = float((weekly.iloc[-1] / ema30w.iloc[-1] - 1) * 100)
        average_traded_value = float((data["Close"] * data["Volume"]).tail(20).mean())
        volatility = float(close.pct_change().tail(60).std() * math.sqrt(252) * 100)

        requirements = {
            "monthly_above_rising_10m_ema": bool(monthly.iloc[-1] > ema10m.iloc[-1] > ema10m.iloc[-2]),
            "weekly_above_rising_30w_ema": bool(weekly.iloc[-1] > ema30w.iloc[-1] > ema30w.iloc[-2]),
            "positive_6m_momentum": momentum_6m > 0,
            "positive_12_to_1m_momentum": momentum_12_1 > 0,
            "positive_relative_strength": relative_strength > 0,
        }
        components = {
            "relative_strength": max(0.0, min(30.0, 15 + relative_strength * 1.5)),
            "momentum_12_1": max(0.0, min(25.0, momentum_12_1 * 0.8)),
            "momentum_6m": max(0.0, min(20.0, momentum_6m * 0.8)),
            "trend_strength": max(0.0, min(15.0, 7.5 + (monthly_spread + weekly_spread) * 0.5)),
            "liquidity_volatility": max(0.0, min(10.0, 10 - max(0, volatility - 25) * 0.2)),
        }
        score = round(sum(components.values()), 2)
        trigger = entry_indicators(data)
        regime = self.regime(index)
        qualified = all(requirements.values())
        ready = qualified and trigger["entry_ready"] and regime["state"] != "RISK_OFF"
        sizing = {"quantity": 0, "position_value": 0, "risk_budget": 0, "estimated_risk": 0,
                  "estimated_cost_bps": estimated_cost_bps}
        if ready:
            sizing = size_position(
                equity=portfolio_equity, entry=trigger["price"], stop=trigger["proposed_stop"],
                risk_pct=regime["risk_per_trade_pct"], estimated_cost_bps=estimated_cost_bps,
            )
            ready = sizing["quantity"] > 0
        return {
            "symbol": symbol, "name": display_name(symbol), "sector": sector,
            "nifty_50_member": symbol in NIFTY_50_SYMBOLS,
            "price": trigger["price"], "as_of": trigger["as_of"],
            "state": "BUY_READY" if ready else "WATCHLIST",
            "score": score, "score_breakdown": {key: round(value, 2) for key, value in components.items()},
            "score_maximums": {"relative_strength": 30, "momentum_12_1": 25, "momentum_6m": 20, "trend_strength": 15, "liquidity_volatility": 10},
            "average_traded_value": round(average_traded_value, 2),
            "features": {
                "momentum_6m": round(momentum_6m, 2), "momentum_12_1": round(momentum_12_1, 2),
                "relative_strength_6m": round(relative_strength, 2), "annualized_volatility": round(volatility, 2),
                **trigger,
            },
            "requirements": requirements,
            "entry": {"price": trigger["price"], "proposed_stop": trigger["proposed_stop"], **sizing},
            "regime": regime,
            "rejection_reasons": [key for key, passed in requirements.items() if not passed]
            + ([] if trigger["entry_ready"] else trigger["rejection_reasons"])
            + (["market_risk_off"] if regime["state"] == "RISK_OFF" else []),
            "reasons": [key.replace("_", " ") for key, passed in requirements.items() if passed][:4],
        }
