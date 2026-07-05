from typing import Any

import pandas as pd

from strategies.base import Strategy


class SmaCrossoverStrategy(Strategy):
    name = "sma_crossover"

    def __init__(self, short_window: int = 20, long_window: int = 50) -> None:
        self.short_window = short_window
        self.long_window = long_window

    def evaluate(self, close: pd.Series) -> dict[str, Any]:
        if len(close) < self.long_window:
            raise ValueError(f"SMA crossover needs at least {self.long_window} data points")

        short = close.rolling(self.short_window).mean()
        long = close.rolling(self.long_window).mean()
        signal = "BUY" if short.iloc[-1] > long.iloc[-1] else "SELL"

        return {
            "signal": signal,
            "indicators": {
                f"sma_{self.short_window}": round(float(short.iloc[-1]), 2),
                f"sma_{self.long_window}": round(float(long.iloc[-1]), 2),
            },
        }
