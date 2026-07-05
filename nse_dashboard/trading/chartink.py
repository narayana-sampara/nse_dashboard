from __future__ import annotations

from typing import Any, Literal

import pandas as pd

from nse_dashboard.trading.indicators import normalized_ohlcv


Timeframe = Literal["weekly", "monthly"]


def _completed_bars(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    data = normalized_ohlcv(frame, minimum_rows=2)
    rule = "W-FRI" if timeframe == "weekly" else "ME"
    bars = data.resample(rule).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    latest = data.index[-1].normalize()
    if timeframe == "weekly" and latest.weekday() < 4:
        bars = bars.iloc[:-1]
    elif timeframe == "monthly" and latest < pd.offsets.BMonthEnd().rollforward(latest).normalize():
        bars = bars.iloc[:-1]
    if len(bars) < 202:
        raise ValueError(f"{timeframe.title()} Chartink screen needs at least 202 completed bars")
    return bars


def _adx(frame: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high, low, close = frame["High"], frame["Low"], frame["Close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat(
        (high - low, (high - close.shift()).abs(), (low - close.shift()).abs()), axis=1
    ).max(axis=1)
    average_range = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    denominator = (plus_di + minus_di).replace(0, 1e-12)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def chartink_macd_trend_signal(frame: pd.DataFrame, timeframe: Timeframe) -> dict[str, Any]:
    """Evaluate the supplied Chartink crossover and its exact bearish inverse."""

    bars = _completed_bars(frame, timeframe)
    close = bars["Close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - macd_signal
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    adx, plus_di, minus_di = _adx(bars, 14)

    buy = {
        "macd_above_zero": macd.iloc[-1] > 0,
        "signal_above_zero": macd_signal.iloc[-1] > 0,
        "bullish_macd_cross": macd.iloc[-1] > macd_signal.iloc[-1]
        and macd.iloc[-2] <= macd_signal.iloc[-2],
        "histogram_rising": histogram.iloc[-1] > histogram.iloc[-2],
        "ema_21_above_50_above_200": ema21.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1],
        "adx_above_25": adx.iloc[-1] > 25,
        "adx_above_positive_di": adx.iloc[-1] > plus_di.iloc[-1],
        "positive_di_above_negative_di": plus_di.iloc[-1] > minus_di.iloc[-1],
    }
    sell = {
        "macd_below_zero": macd.iloc[-1] < 0,
        "signal_below_zero": macd_signal.iloc[-1] < 0,
        "bearish_macd_cross": macd.iloc[-1] < macd_signal.iloc[-1]
        and macd.iloc[-2] >= macd_signal.iloc[-2],
        "histogram_falling": histogram.iloc[-1] < histogram.iloc[-2],
        "ema_21_below_50_below_200": ema21.iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1],
        "adx_above_25": adx.iloc[-1] > 25,
        "adx_above_negative_di": adx.iloc[-1] > minus_di.iloc[-1],
        "negative_di_above_positive_di": minus_di.iloc[-1] > plus_di.iloc[-1],
    }
    signal = "BUY" if all(buy.values()) else "SELL" if all(sell.values()) else "HOLD"
    direction = 1 if signal == "BUY" else -1
    directional_spread = direction * (float(plus_di.iloc[-1]) - float(minus_di.iloc[-1]))
    ema_spread = direction * (float(ema21.iloc[-1]) / float(ema200.iloc[-1]) - 1) * 100
    acceleration = direction * float(histogram.iloc[-1] - histogram.iloc[-2])
    acceleration_pct = acceleration / max(float(close.iloc[-1]), 1e-12) * 10_000
    strength = max(
        0.0,
        min(100.0, 50 + min(15, max(0, float(adx.iloc[-1]) - 25))
            + min(15, max(0, directional_spread))
            + min(10, max(0, ema_spread))
            + min(10, max(0, acceleration_pct))),
    ) if signal != "HOLD" else 0.0
    active_conditions = buy if signal != "SELL" else sell
    return {
        "timeframe": timeframe,
        "signal": signal,
        "strength_score": round(strength, 2),
        "as_of": bars.index[-1].date().isoformat(),
        "conditions": active_conditions,
        "rejection_reasons": [name for name, passed in active_conditions.items() if not passed],
        "features": {
            "macd": round(float(macd.iloc[-1]), 4),
            "macd_signal": round(float(macd_signal.iloc[-1]), 4),
            "macd_histogram": round(float(histogram.iloc[-1]), 4),
            "ema_21": round(float(ema21.iloc[-1]), 2),
            "ema_50": round(float(ema50.iloc[-1]), 2),
            "ema_200": round(float(ema200.iloc[-1]), 2),
            "adx_14": round(float(adx.iloc[-1]), 2),
            "positive_di_14": round(float(plus_di.iloc[-1]), 2),
            "negative_di_14": round(float(minus_di.iloc[-1]), 2),
        },
    }
