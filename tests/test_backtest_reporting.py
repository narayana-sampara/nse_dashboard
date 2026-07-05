import numpy as np
import pandas as pd

from nse_dashboard.trading.backtest import ClosedTrade, backtest_report, promotion_assessment


def test_backtest_report_and_promotion_gates_are_explicit() -> None:
    equity = pd.Series(100_000 * np.cumprod(np.full(300, 1.0005)))
    trades = [ClosedTrade(f"S{i}", 10_000, 100 if i % 4 else -50, f"Sector{i % 5}") for i in range(160)]
    report = backtest_report(equity, trades)
    assessment = promotion_assessment(report, parameter_neighbors_profitable=True)
    assert report["trade_count"] == 160
    assert report["expectancy"] > 0
    assert set(assessment["gates"]) >= {"positive_expectancy", "at_least_150_trades"}
