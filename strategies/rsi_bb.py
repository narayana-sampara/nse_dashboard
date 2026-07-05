from typing import Any

import pandas as pd

from strategies.base import Strategy


class RsiBollingerStrategy(Strategy):
    name = "rsi_bb"

    def __init__(self, window: int = 20, rsi_window: int = 14) -> None:
        self.window = window
        self.rsi_window = rsi_window

    def evaluate(self, close: pd.Series) -> dict[str, Any]:
        required = max(self.window, self.rsi_window) + 1
        if len(close) < required:
            raise ValueError(f"RSI/Bollinger strategy needs at least {required} data points")

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / self.rsi_window, adjust=False).mean()
        loss = -delta.clip(upper=0).ewm(alpha=1 / self.rsi_window, adjust=False).mean()
        relative_strength = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + relative_strength))

        middle = close.rolling(self.window).mean()
        deviation = close.rolling(self.window).std()
        upper = middle + 2 * deviation
        lower = middle - 2 * deviation

        latest_close = float(close.iloc[-1])
        latest_rsi = float(rsi.iloc[-1])
        if latest_rsi < 30 and latest_close < float(lower.iloc[-1]):
            signal = "BUY"
        elif latest_rsi > 70 and latest_close > float(upper.iloc[-1]):
            signal = "SELL"
        else:
            signal = "HOLD"

        return {
            "signal": signal,
            "indicators": {
                "rsi": round(latest_rsi, 2),
                "bb_upper": round(float(upper.iloc[-1]), 2),
                "bb_middle": round(float(middle.iloc[-1]), 2),
                "bb_lower": round(float(lower.iloc[-1]), 2),
            },
        }
