from typing import Any

import pandas as pd


class CompositeTechnicalStrategy:
    """Score price action from -100 (bearish) to +100 (bullish).

    The weights deliberately combine independent indicator families instead of
    treating one indicator as a trading system. The returned reasons make the
    ranking auditable in the UI.
    """

    name = "composite_technical"
    minimum_rows = 210

    def evaluate(self, frame: pd.DataFrame) -> dict[str, Any]:
        frame = frame.dropna(subset=["Close"])
        if len(frame) < self.minimum_rows:
            raise ValueError(f"Composite strategy needs {self.minimum_rows} sessions")

        close = frame["Close"].astype(float)
        volume = frame["Volume"].astype(float)
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        relative_strength = gain / loss.where(loss != 0, 1e-12)
        rsi = 100 - (100 / (1 + relative_strength))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()

        middle = close.rolling(20).mean()
        deviation = close.rolling(20).std()
        upper = middle + 2 * deviation
        lower = middle - 2 * deviation
        band_width = (upper - lower).replace(0, float("nan"))
        band_position = (close - lower) / band_width

        momentum20 = close.pct_change(20) * 100
        volume_ratio = volume / volume.rolling(20).mean()

        price = float(close.iloc[-1])
        score = 0
        reasons: list[str] = []

        # Long-term and medium-term trend: 30 points.
        if price > float(ema200.iloc[-1]):
            score += 15
            reasons.append("above 200-day trend")
        else:
            score -= 15
            reasons.append("below 200-day trend")
        if float(ema20.iloc[-1]) > float(ema50.iloc[-1]):
            score += 15
            reasons.append("20 EMA above 50 EMA")
        else:
            score -= 15
            reasons.append("20 EMA below 50 EMA")

        # MACD direction: 20 points.
        if float(macd.iloc[-1]) > float(macd_signal.iloc[-1]):
            score += 20
            reasons.append("MACD bullish")
        else:
            score -= 20
            reasons.append("MACD bearish")

        # RSI regime: 20 points, avoiding mechanically buying overbought moves.
        latest_rsi = float(rsi.iloc[-1])
        if 50 <= latest_rsi <= 70:
            score += 20
            reasons.append("RSI confirms strength")
        elif 30 <= latest_rsi < 50:
            score -= 10
            reasons.append("RSI below momentum midpoint")
        elif latest_rsi < 30:
            score -= 20
            reasons.append("RSI oversold; trend risk")
        else:
            score += 5
            reasons.append("RSI overbought; reduced weight")

        # One-month price momentum: 20 points.
        latest_momentum = float(momentum20.iloc[-1])
        if latest_momentum > 3:
            score += 20
            reasons.append("positive 20-day momentum")
        elif latest_momentum < -3:
            score -= 20
            reasons.append("negative 20-day momentum")

        # Bollinger location and volume confirmation: 10 points combined.
        latest_band_position = float(band_position.iloc[-1])
        if latest_band_position >= 0.6:
            score += 5
        elif latest_band_position <= 0.4:
            score -= 5

        latest_volume_ratio = float(volume_ratio.iloc[-1])
        if latest_volume_ratio >= 1.2:
            score += 5 if score >= 0 else -5
            reasons.append("volume confirms move")

        score = max(-100, min(100, score))
        signal = "BUY" if score >= 25 else "SELL" if score <= -25 else "HOLD"
        confidence = min(99, 50 + abs(score) // 2)

        return {
            "signal": signal,
            "score": score,
            "confidence": confidence,
            "price": round(price, 2),
            "change_pct": round(float(close.pct_change().iloc[-1] * 100), 2),
            "as_of": close.index[-1].date().isoformat(),
            "indicators": {
                "rsi": round(latest_rsi, 1),
                "momentum_20d": round(latest_momentum, 2),
                "volume_ratio": round(latest_volume_ratio, 2),
                "ema_20": round(float(ema20.iloc[-1]), 2),
                "ema_50": round(float(ema50.iloc[-1]), 2),
                "ema_200": round(float(ema200.iloc[-1]), 2),
            },
            "reasons": reasons[:4],
        }
