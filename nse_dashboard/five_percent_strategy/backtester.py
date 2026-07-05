from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from nse_dashboard.five_percent_strategy.baseline_model import ExplainableBaselineModel
from nse_dashboard.five_percent_strategy.features import MINIMUM_ROWS, compute_features
from nse_dashboard.five_percent_strategy.models import Trade


@dataclass(slots=True)
class BacktestConfig:
    start_date: str
    end_date: str
    initial_capital: float = 10_000.0
    target_pct: float = 5.0
    stop_loss_pct: float = 2.0
    holding_days: int = 5
    probability_threshold: float = 65.0
    max_trades: int = 200
    cost_bps: float = 30.0
    slippage_bps: float = 10.0
    diversify: bool = False
    max_concurrent_trades: int = 5


@dataclass(slots=True)
class _Signal:
    symbol: str
    signal_date: pd.Timestamp
    entry_index: int
    frame: pd.DataFrame
    probability_score: float
    ai_score: float


def _generate_signals(
    histories: dict[str, pd.DataFrame],
    config: BacktestConfig,
    model: ExplainableBaselineModel,
) -> list[_Signal]:
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date)
    signals: list[_Signal] = []
    for symbol, frame in histories.items():
        frame = frame.dropna(subset=["Close", "Volume"]).sort_index()
        if len(frame) < MINIMUM_ROWS + 1:
            continue
        for i in range(MINIMUM_ROWS - 1, len(frame) - 1):
            index_date = frame.index[i]
            ts = pd.Timestamp(index_date)
            if ts < start or ts > end:
                continue
            window = frame.iloc[: i + 1]
            try:
                features = compute_features(symbol, window)
            except ValueError:
                continue
            prediction = model.predict_candidates([features])[0]
            if prediction.probability_score < config.probability_threshold:
                continue
            signals.append(
                _Signal(
                    symbol=symbol,
                    signal_date=ts,
                    entry_index=i,
                    frame=frame,
                    probability_score=prediction.probability_score,
                    ai_score=prediction.ai_score,
                )
            )
    signals.sort(key=lambda item: item.signal_date)
    return signals


def _simulate_trade(
    signal: _Signal,
    config: BacktestConfig,
    capital_before: float,
) -> Trade | None:
    frame = signal.frame
    entry_index = signal.entry_index
    if entry_index + 1 >= len(frame):
        return None
    entry_row = frame.iloc[entry_index + 1]
    entry_price = float(entry_row["Open"]) if "Open" in entry_row else float(entry_row["Close"])
    slippage_mult = 1 + config.slippage_bps / 10_000
    entry_price *= slippage_mult
    target_price = entry_price * (1 + config.target_pct / 100)
    stop_price = entry_price * (1 - config.stop_loss_pct / 100)

    window = frame.iloc[entry_index + 1 : entry_index + 1 + config.holding_days]
    exit_price: float | None = None
    exit_reason = "holding_period_expiry"
    exit_date = None
    holding_days_used = len(window)
    for offset, (idx, row) in enumerate(window.iterrows(), start=1):
        low = float(row["Low"]) if "Low" in row else float(row["Close"])
        high = float(row["High"]) if "High" in row else float(row["Close"])
        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
            exit_date = idx
            holding_days_used = offset
            break
        if high >= target_price:
            exit_price = target_price
            exit_reason = "target_hit"
            exit_date = idx
            holding_days_used = offset
            break
    if exit_price is None:
        if len(window) == 0:
            return None
        exit_price = float(window.iloc[-1]["Close"])
        exit_date = window.index[-1]

    exit_price *= 1 - config.slippage_bps / 10_000
    cost_mult = 1 - config.cost_bps / 10_000
    gross_return_pct = (exit_price / entry_price - 1) * 100
    net_return_pct = gross_return_pct + (cost_mult - 1) * 100

    capital_after = capital_before * (1 + net_return_pct / 100)
    exit_date_str = exit_date.date().isoformat() if hasattr(exit_date, "date") else str(exit_date)

    return Trade(
        symbol=signal.symbol,
        entry_date=signal.signal_date.date().isoformat(),
        exit_date=exit_date_str,
        entry_price=round(entry_price, 2),
        exit_price=round(exit_price, 2),
        target_price=round(target_price, 2),
        stop_loss_price=round(stop_price, 2),
        result="win" if net_return_pct > 0 else "loss",
        return_pct=round(net_return_pct, 3),
        capital_before=round(capital_before, 2),
        capital_after=round(capital_after, 2),
        holding_days=holding_days_used,
        exit_reason=exit_reason,
        probability_score=round(signal.probability_score, 2),
        ai_score=round(signal.ai_score, 2),
    )


def run_backtest(
    histories: dict[str, pd.DataFrame],
    config: BacktestConfig,
    model: ExplainableBaselineModel | None = None,
) -> dict[str, Any]:
    """Simulate the 5% growth strategy over historical data.

    Compounding mode (default) keeps a single active trade at a time: capital
    from a closed trade is reinvested into the next signal. Diversified mode
    spreads capital across up to ``max_concurrent_trades`` symbols at once.
    """

    model = model or ExplainableBaselineModel(
        target_pct=config.target_pct, stop_loss_pct=config.stop_loss_pct
    )
    signals = _generate_signals(histories, config, model)

    trades: list[Trade] = []
    equity_curve: list[dict[str, Any]] = []

    if config.diversify:
        capital_per_slot = config.initial_capital / max(1, config.max_concurrent_trades)
        slot_capital = [capital_per_slot] * config.max_concurrent_trades
        busy_until: list[pd.Timestamp | None] = [None] * config.max_concurrent_trades
        for signal in signals:
            if len(trades) >= config.max_trades:
                break
            slot = next(
                (i for i in range(config.max_concurrent_trades) if busy_until[i] is None or busy_until[i] <= signal.signal_date),
                None,
            )
            if slot is None:
                continue
            trade = _simulate_trade(signal, config, slot_capital[slot])
            if trade is None:
                continue
            slot_capital[slot] = trade.capital_after
            busy_until[slot] = pd.Timestamp(trade.exit_date) if trade.exit_date else signal.signal_date
            trades.append(trade)
            equity_curve.append({"date": trade.exit_date, "capital": round(sum(slot_capital), 2)})
        final_capital = sum(slot_capital)
    else:
        capital = config.initial_capital
        busy_until: pd.Timestamp | None = None
        for signal in signals:
            if len(trades) >= config.max_trades:
                break
            if busy_until is not None and signal.signal_date <= busy_until:
                continue
            trade = _simulate_trade(signal, config, capital)
            if trade is None:
                continue
            capital = trade.capital_after
            busy_until = pd.Timestamp(trade.exit_date) if trade.exit_date else signal.signal_date
            trades.append(trade)
            equity_curve.append({"date": trade.exit_date, "capital": round(capital, 2)})
        final_capital = capital

    return _summarize(trades, equity_curve, config, final_capital)


def _summarize(
    trades: list[Trade],
    equity_curve: list[dict[str, Any]],
    config: BacktestConfig,
    final_capital: float,
) -> dict[str, Any]:
    winning = [t for t in trades if t.result == "win"]
    losing = [t for t in trades if t.result == "loss"]
    total_return_pct = (final_capital / config.initial_capital - 1) * 100 if config.initial_capital else 0.0

    gross_profit = sum(t.capital_after - t.capital_before for t in winning)
    gross_loss = abs(sum(t.capital_after - t.capital_before for t in losing))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    peak = config.initial_capital
    max_drawdown_pct = 0.0
    for point in equity_curve:
        peak = max(peak, point["capital"])
        drawdown = (point["capital"] / peak - 1) * 100 if peak else 0.0
        max_drawdown_pct = min(max_drawdown_pct, drawdown)

    longest_win_streak = _longest_streak(trades, "win")
    longest_loss_streak = _longest_streak(trades, "loss")

    return {
        "final_capital": round(final_capital, 2),
        "total_return_pct": round(total_return_pct, 2),
        "total_trades": len(trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(len(winning) / len(trades) * 100, 2) if trades else 0.0,
        "average_win_pct": round(sum(t.return_pct for t in winning) / len(winning), 3) if winning else 0.0,
        "average_loss_pct": round(sum(t.return_pct for t in losing) / len(losing), 3) if losing else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "profit_factor": None if profit_factor == float("inf") else round(profit_factor, 3),
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "equity_curve": equity_curve,
        "trades": [
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "target_price": t.target_price,
                "stop_loss_price": t.stop_loss_price,
                "result": t.result,
                "return_pct": t.return_pct,
                "capital_before": t.capital_before,
                "capital_after": t.capital_after,
                "holding_days": t.holding_days,
                "exit_reason": t.exit_reason,
                "probability_score": t.probability_score,
                "ai_score": t.ai_score,
            }
            for t in trades
        ],
    }


def _longest_streak(trades: list[Trade], result: str) -> int:
    longest = current = 0
    for trade in trades:
        if trade.result == result:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
