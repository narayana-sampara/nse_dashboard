from __future__ import annotations

import math

import pandas as pd

from nse_dashboard.five_percent_strategy.models import StockFeatureRow

MINIMUM_ROWS = 60


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.where(loss != 0, 1e-12)))


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def compute_features(
    symbol: str,
    frame: pd.DataFrame,
    *,
    nifty_frame: pd.DataFrame | None = None,
    sector: str | None = None,
    company_name: str | None = None,
) -> StockFeatureRow:
    """Compute point-in-time technical features from OHLCV history.

    Every value derives only from data up to and including the last row of
    ``frame`` (no forward-looking columns), so callers can safely reuse this
    for both live scans and walk-forward backtests by slicing history first.
    """

    frame = frame.dropna(subset=["Close", "Volume"]).copy()
    if len(frame) < MINIMUM_ROWS:
        raise ValueError(f"{symbol} needs at least {MINIMUM_ROWS} sessions of history")

    close = frame["Close"].astype(float)
    volume = frame["Volume"].astype(float)
    open_ = frame["Open"].astype(float) if "Open" in frame else close
    returns = close.pct_change()

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    rsi14 = _rsi(close)
    atr14 = _atr(frame) if {"High", "Low"}.issubset(frame.columns) else (close.diff().abs().ewm(alpha=1 / 14, adjust=False).mean())

    avg_volume_20d = float(volume.tail(20).mean())
    avg_traded_value_20d = float((close * volume).tail(20).mean())
    volume_ratio = float(volume.iloc[-1] / max(avg_volume_20d, 1e-9))
    volatility = float(returns.rolling(20).std().iloc[-1] * math.sqrt(252) * 100) if len(returns.dropna()) >= 20 else 0.0

    window_20_high = close.rolling(20).max()
    breakout_20d_high = bool(close.iloc[-1] >= window_20_high.iloc[-1] * 0.999)

    window_252 = close.tail(252)
    high_52w = float(window_252.max())
    distance_from_52w_high_pct = float((close.iloc[-1] / max(high_52w, 1e-9) - 1) * 100)

    recent_high = float(close.tail(60).max())
    drawdown_from_recent_high_pct = float((close.iloc[-1] / max(recent_high, 1e-9) - 1) * 100)

    gap_pct = float((open_.iloc[-1] / max(float(close.iloc[-2]), 1e-9) - 1) * 100) if len(close) >= 2 else 0.0

    relative_strength_vs_nifty = 0.0
    if nifty_frame is not None and not nifty_frame.empty and "Close" in nifty_frame:
        nifty_close = nifty_frame["Close"].astype(float).dropna()
        if len(nifty_close) >= 6 and len(close) >= 6:
            stock_5d = float(close.pct_change(5).iloc[-1] * 100)
            nifty_5d = float(nifty_close.pct_change(5).iloc[-1] * 100)
            relative_strength_vs_nifty = stock_5d - nifty_5d

    as_of = close.index[-1]
    as_of_str = as_of.date().isoformat() if hasattr(as_of, "date") else str(as_of)

    return StockFeatureRow(
        symbol=symbol,
        as_of=as_of_str,
        close=float(close.iloc[-1]),
        company_name=company_name,
        sector=sector,
        return_1d=float(close.pct_change(1).iloc[-1] * 100),
        return_3d=float(close.pct_change(3).iloc[-1] * 100) if len(close) > 3 else 0.0,
        return_5d=float(close.pct_change(5).iloc[-1] * 100) if len(close) > 5 else 0.0,
        return_20d=float(close.pct_change(20).iloc[-1] * 100) if len(close) > 20 else 0.0,
        momentum_5d=float(close.pct_change(5).iloc[-1] * 100) if len(close) > 5 else 0.0,
        momentum_20d=float(close.pct_change(20).iloc[-1] * 100) if len(close) > 20 else 0.0,
        ema_9=float(ema9.iloc[-1]),
        ema_20=float(ema20.iloc[-1]),
        ema_50=float(ema50.iloc[-1]),
        rsi_14=float(rsi14.iloc[-1]),
        atr_14=float(atr14.iloc[-1]),
        volume_ratio=volume_ratio,
        avg_volume_20d=avg_volume_20d,
        avg_traded_value_20d=avg_traded_value_20d,
        volatility=volatility,
        gap_pct=gap_pct,
        relative_strength_vs_nifty=relative_strength_vs_nifty,
        breakout_20d_high=breakout_20d_high,
        distance_from_52w_high_pct=distance_from_52w_high_pct,
        drawdown_from_recent_high_pct=drawdown_from_recent_high_pct,
    )


def label_hits_target_before_stop(
    frame: pd.DataFrame,
    entry_index: int,
    *,
    target_pct: float = 5.0,
    stop_loss_pct: float = 2.0,
    holding_days: int = 5,
) -> int:
    """ML training label: 1 if +target_pct is reached before -stop_loss_pct within holding_days.

    Uses only rows strictly after ``entry_index`` (no look-ahead into the entry bar itself).
    """

    if entry_index < 0 or entry_index >= len(frame) - 1:
        raise ValueError("entry_index must leave at least one future row in frame")

    entry_price = float(frame["Close"].iloc[entry_index])
    target_price = entry_price * (1 + target_pct / 100)
    stop_price = entry_price * (1 - stop_loss_pct / 100)

    window = frame.iloc[entry_index + 1 : entry_index + 1 + holding_days]
    for _, row in window.iterrows():
        high = float(row["High"]) if "High" in row else float(row["Close"])
        low = float(row["Low"]) if "Low" in row else float(row["Close"])
        if low <= stop_price:
            return 0
        if high >= target_price:
            return 1
    return 0
