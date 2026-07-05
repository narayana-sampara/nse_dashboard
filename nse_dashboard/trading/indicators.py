from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


OHLCV = ("Open", "High", "Low", "Close", "Volume")


def normalized_ohlcv(frame: pd.DataFrame, minimum_rows: int = 35) -> pd.DataFrame:
    # Close-only fixtures and legacy adapters remain readable; production
    # providers are still expected to supply true OHLC bars.
    if "Close" in frame.columns:
        frame = frame.copy()
        for name in ("Open", "High", "Low"):
            if name not in frame.columns:
                frame[name] = frame["Close"]
    missing = [name for name in OHLCV if name not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV data missing columns: {', '.join(missing)}")
    result = frame.loc[:, OHLCV].copy()
    result.index = pd.to_datetime(result.index)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    result = result.apply(pd.to_numeric, errors="coerce").dropna()
    if len(result) < minimum_rows:
        raise ValueError(f"At least {minimum_rows} complete OHLCV sessions are required")
    if (result[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (result["Volume"] < 0).any():
        raise ValueError("Volume must not be negative")
    return result


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = frame["High"], frame["Low"], frame["Close"]
    tr = pd.concat(
        (high - low, (high - close.shift()).abs(), (low - close.shift()).abs()), axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def supertrend(
    frame: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    atr = wilder_atr(frame, period)
    midpoint = (frame["High"] + frame["Low"]) / 2
    basic_upper = midpoint + multiplier * atr
    basic_lower = midpoint - multiplier * atr
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    bullish = pd.Series(False, index=frame.index, dtype=bool)
    start = next((i for i, value in enumerate(atr.notna()) if value), None)
    if start is None:
        return pd.DataFrame({"line": np.nan, "bullish": bullish, "flip": False}, index=frame.index)
    bullish.iloc[start] = frame["Close"].iloc[start] >= midpoint.iloc[start]
    for i in range(start + 1, len(frame)):
        if basic_upper.iloc[i] < final_upper.iloc[i - 1] or frame["Close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]
        if basic_lower.iloc[i] > final_lower.iloc[i - 1] or frame["Close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]
        if bullish.iloc[i - 1]:
            bullish.iloc[i] = frame["Close"].iloc[i] >= final_lower.iloc[i]
        else:
            bullish.iloc[i] = frame["Close"].iloc[i] > final_upper.iloc[i]
    line = final_lower.where(bullish, final_upper)
    flip = bullish.ne(bullish.shift()).fillna(False)
    flip.iloc[: start + 1] = False
    return pd.DataFrame({"line": line, "bullish": bullish, "flip": flip}, index=frame.index)


def entry_indicators(frame: pd.DataFrame) -> dict[str, Any]:
    data = normalized_ohlcv(frame)
    close, volume = data["Close"], data["Volume"]
    atr = wilder_atr(data, 14)
    rsi = wilder_rsi(close, 14)
    trend = supertrend(data, 10, 3.0)
    prior_volume = volume.shift().rolling(20).mean()
    prior_high = data["High"].shift().rolling(20).max()
    ema20 = close.ewm(span=20, adjust=False).mean()
    recent_flip = bool((trend["flip"] & trend["bullish"]).tail(5).any())
    volume_ratio = float(volume.iloc[-1] / max(float(prior_volume.iloc[-1]), 1.0))
    extension = float((close.iloc[-1] / ema20.iloc[-1] - 1) * 100)
    latest_rsi = float(rsi.iloc[-1])
    breakout = bool(close.iloc[-1] > prior_high.iloc[-1])
    conditions = {
        "supertrend_bullish": bool(trend["bullish"].iloc[-1]),
        "recent_bullish_flip_or_breakout": recent_flip or breakout,
        "rsi_50_70": 50 <= latest_rsi <= 70,
        "volume_confirmation": volume_ratio > 1.5,
        "extension_within_8pct": extension <= 8,
    }
    ready = all(conditions.values())
    stop = max(float(trend["line"].iloc[-1]), float(close.iloc[-1] - 2 * atr.iloc[-1]))
    stop_distance_pct = float((close.iloc[-1] - stop) / close.iloc[-1] * 100)
    if not 1 <= stop_distance_pct <= 8:
        ready = False
        conditions["stop_distance_1_8pct"] = False
    else:
        conditions["stop_distance_1_8pct"] = True
    return {
        "as_of": data.index[-1].date().isoformat(),
        "price": round(float(close.iloc[-1]), 2),
        "atr_14": round(float(atr.iloc[-1]), 4),
        "rsi_14": round(latest_rsi, 2),
        "supertrend": round(float(trend["line"].iloc[-1]), 2),
        "supertrend_bullish": conditions["supertrend_bullish"],
        "recent_bullish_flip": recent_flip,
        "breakout_20d": breakout,
        "volume_ratio": round(volume_ratio, 2),
        "ema20_extension_pct": round(extension, 2),
        "proposed_stop": round(stop, 2),
        "stop_distance_pct": round(stop_distance_pct, 2),
        "entry_ready": ready,
        "conditions": conditions,
        "rejection_reasons": [name for name, passed in conditions.items() if not passed],
    }


def market_regime(frame: pd.DataFrame) -> dict[str, Any]:
    data = normalized_ohlcv(frame, minimum_rows=220)
    close = data["Close"]
    monthly = close.resample("ME").last().dropna()
    latest = data.index[-1].normalize()
    if latest < pd.offsets.BMonthEnd().rollforward(latest).normalize():
        monthly = monthly.iloc[:-1]
    ema10m = monthly.ewm(span=10, adjust=False).mean()
    ema200d = close.ewm(span=200, adjust=False).mean()
    above_rising_10m = bool(len(monthly) >= 11 and monthly.iloc[-1] > ema10m.iloc[-1] and ema10m.iloc[-1] > ema10m.iloc[-2])
    above_200d = bool(close.iloc[-1] > ema200d.iloc[-1])
    passed = int(above_rising_10m) + int(above_200d)
    regime = "RISK_ON" if passed == 2 else "NEUTRAL" if passed == 1 else "RISK_OFF"
    return {
        "state": regime,
        "as_of": data.index[-1].date().isoformat(),
        "above_rising_10m_ema": above_rising_10m,
        "above_200d_ema": above_200d,
        "maximum_exposure_pct": 80 if regime == "RISK_ON" else 40 if regime == "NEUTRAL" else 0,
        "risk_per_trade_pct": 0.5 if regime == "RISK_ON" else 0.25 if regime == "NEUTRAL" else 0,
    }
