from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from nse_dashboard.trading.indicators import entry_indicators, market_regime, wilder_atr, wilder_rsi
from nse_dashboard.trading.portfolio import PaperPortfolio, PaperPosition, size_position


def bars(rows: int = 320, volume: float = 1_000_000) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = np.linspace(100, 150, rows)
    return pd.DataFrame(
        {"Open": close - .2, "High": close + 1, "Low": close - 1,
         "Close": close, "Volume": np.full(rows, volume)}, index=index
    )


def test_wilder_indicators_and_volume_use_only_prior_sessions() -> None:
    frame = bars()
    frame.loc[frame.index[-1], "Volume"] = 2_000_000
    result = entry_indicators(frame)
    assert round(result["volume_ratio"], 2) == 2.0
    assert wilder_atr(frame).iloc[-1] > 0
    assert 0 <= wilder_rsi(frame["Close"]).iloc[-1] <= 100


def test_regime_contract_has_exposure_and_risk_limits() -> None:
    result = market_regime(bars())
    assert result["state"] in {"RISK_ON", "NEUTRAL", "RISK_OFF"}
    assert result["maximum_exposure_pct"] in {0, 40, 80}


def test_position_sizing_caps_position_value() -> None:
    result = size_position(equity=1_000_000, entry=100, stop=95, risk_pct=.5)
    assert result["quantity"] > 0
    assert result["position_value"] <= 100_000


def test_rsi_partial_exit_occurs_only_once() -> None:
    position = PaperPosition("TEST.NS", "Test", date.today(), 100, 100, 95, 95, 100)
    first = position.evaluate_close(close=110, atr=2, supertrend=104, rsi=76,
                                    below_supertrend_days=0, weekly_below_ema10=False)
    second = position.evaluate_close(close=111, atr=2, supertrend=105, rsi=78,
                                     below_supertrend_days=0, weekly_below_ema10=False)
    assert first["action"] == "PARTIAL_EXIT"
    assert second["action"] == "HOLD"


def test_drawdown_circuit_breaker_pauses_portfolio() -> None:
    portfolio = PaperPortfolio(cash=900_000, high_water_mark=1_000_000)
    summary = portfolio.summary()
    assert summary["liquidate"] is True
    assert summary["new_entries_allowed"] is False
