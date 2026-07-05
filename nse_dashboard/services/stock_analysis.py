from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from nse_dashboard.core.json import json_ready
from nse_dashboard.domain.alpha import normalize_symbol
from nse_dashboard.services.alpha_ranking import AlphaRankingService, _feature_set
from nse_dashboard.services.monthly_predictions import ExplainableMonthlyModel
from nse_dashboard.services.weekly_predictions import ExplainableWeeklyModel
from nse_dashboard.trading.chartink import chartink_macd_trend_signal
from nse_dashboard.trading.indicators import entry_indicators, market_regime
from sector_map import display_name, get_sector


class SingleStockAnalysisService:
    """On-demand technical analysis enriched with locally persisted alpha factors."""

    deep_dive_cache_seconds = 900

    def __init__(
        self,
        adapter: Any,
        snapshots: Any,
        cache: Any | None = None,
        period: str = "max",
    ) -> None:
        self.adapter = adapter
        self.snapshots = snapshots
        self.cache = cache
        self.period = period
        self.alpha = AlphaRankingService(snapshots)
        self.weekly_model = ExplainableWeeklyModel()
        self.monthly_model = ExplainableMonthlyModel()

    def analyze(self, symbol: str) -> dict[str, Any]:
        symbol = normalize_symbol(symbol)
        benchmark_symbol = "^CNX100"
        histories = self.adapter.market_history(
            [symbol, benchmark_symbol], self.period
        )
        frame = histories[symbol]
        benchmark = histories[benchmark_symbol]
        sector = get_sector(symbol)
        errors: list[str] = []

        weekly = self._safe(
            lambda: self.weekly_model.predict(symbol, sector, frame),
            errors,
            "weekly model",
        )
        monthly = self._safe(
            lambda: self.monthly_model.predict(symbol, sector, frame, 1),
            errors,
            "monthly model",
        )
        entry = self._safe(lambda: entry_indicators(frame), errors, "entry indicators")
        if entry is not None:
            entry = dict(entry)
            stop = entry.get("proposed_stop")
            price = entry.get("price")
            if stop is not None and price is not None and float(stop) >= float(price):
                entry["proposed_stop"] = None
                errors.append(
                    "ATR stop: no valid long protective stop while trend resistance is above price"
                )
        regime = self._safe(lambda: market_regime(benchmark), errors, "market regime")
        weekly_indicator = self._safe(
            lambda: chartink_macd_trend_signal(frame, "weekly"),
            errors,
            "weekly crossover",
        )
        monthly_indicator = self._safe(
            lambda: chartink_macd_trend_signal(frame, "monthly"),
            errors,
            "monthly crossover",
        )

        technical_candidate = dict(monthly or weekly or {})
        technical_candidate.update(
            {
                "symbol": symbol,
                "name": display_name(symbol),
                "sector": sector,
                "score": float(
                    (monthly or {}).get(
                        "score", (weekly or {}).get("ranking_score", 0)
                    )
                ),
                "features": {
                    **dict((weekly or {}).get("features", {})),
                    **dict((monthly or {}).get("features", {})),
                    **dict(entry or {}),
                },
                "entry": {
                    "proposed_stop": (entry or {}).get("proposed_stop"),
                },
                "entry_allowed": bool((entry or {}).get("entry_ready"))
                and (regime or {}).get("state") != "RISK_OFF",
            }
        )
        loader = getattr(self.snapshots, "latest_alpha_features", None)
        raw = loader([symbol]).get(symbol, {}) if loader else {}
        alpha = self.alpha.score_candidate(
            technical_candidate,
            _feature_set(raw),
            require_fundamentals=False,
        )
        indication = _indication(
            weekly_indicator=weekly_indicator,
            monthly_indicator=monthly_indicator,
            entry=entry,
            regime=regime,
        )
        price = (
            (entry or {}).get("price")
            or (weekly or {}).get("price")
            or (monthly or {}).get("price")
        )
        return json_ready(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "name": display_name(symbol),
                "sector": sector,
                "source": self.adapter.name,
                "price": price,
                "as_of": (entry or {}).get("as_of")
                or (weekly or {}).get("as_of")
                or (monthly or {}).get("as_of"),
                "indication": indication,
                "market_regime": regime,
                "entry": entry,
                "weekly": weekly,
                "monthly": monthly,
                "weekly_indicator": weekly_indicator,
                "monthly_indicator": monthly_indicator,
                "alpha": alpha,
                "data_warnings": errors,
                "disclaimer": (
                    "Quantitative research signal only. It is not investment advice, "
                    "a recommendation, or a guarantee of returns."
                ),
            }
        )

    def deep_dive(
        self,
        symbol: str,
        *,
        horizon: str = "15d",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        symbol = normalize_symbol(symbol)
        horizon = _normalize_horizon(horizon)
        cache_key = f"analysis:stock:{symbol}:{horizon}:v1"
        if self.cache is not None and force_refresh:
            self.cache.delete(cache_key)
        if self.cache is not None and not force_refresh:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        errors: list[str] = []
        histories = self.adapter.market_history([symbol], self.period)
        frame = histories[symbol]
        technical = _technical_breakdown(frame)
        raw_features = _latest_alpha_features(self.snapshots, symbol)
        fundamental = _fundamental_breakdown(raw_features.get("fundamental"))
        smart_money = _smart_money_breakdown(raw_features.get("options"))
        sentiment = _sentiment_legal_breakdown(
            raw_features.get("sentiment"), raw_features.get("legal")
        )
        weighted = _weighted_score(
            {
                "technical": technical["score"],
                "fundamental": fundamental["score"],
                "smart_money": smart_money["score"],
                "sentiment_legal": sentiment["score"],
            }
        )
        score = weighted["overall_score"]
        projected = _projected_returns(
            frame,
            score,
            {
                "fundamental": fundamental["score"],
                "smart_money": smart_money["score"],
                "sentiment_legal": sentiment["score"],
            },
        )
        result = {
            "schema_version": "deep-dive-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "name": display_name(symbol),
            "sector": get_sector(symbol),
            "source": self.adapter.name,
            "as_of": technical["as_of"],
            "price": technical["price"],
            "requested_horizon": horizon,
            "overall_signal": _overall_signal(score),
            "overall_score": score,
            "confidence_interval": _confidence_interval(
                [
                    technical["score"],
                    fundamental["score"] if fundamental["coverage"] != "MISSING" else None,
                    smart_money["score"] if smart_money["coverage"] != "MISSING" else None,
                    sentiment["score"] if sentiment["coverage"] != "MISSING" else None,
                ]
            ),
            "weights": {
                "technical": 0.35,
                "fundamental": 0.30,
                "smart_money_options": 0.25,
                "news_sentiment_legal": 0.10,
            },
            "score_contributions": weighted["contributions"],
            "factor_breakdown": {
                "technical": technical,
                "fundamental": fundamental,
                "smart_money_options": smart_money,
                "news_sentiment_legal": sentiment,
            },
            "projected_returns": projected,
            "data_warnings": errors + projected.get("warnings", []),
            "methodology": {
                "projection": (
                    "Historical percentile matching over the last five years. "
                    "The engine finds up to the last 50 sessions where the rolling "
                    "composite score was within +/-5 points of today's score. "
                    "Historical composite scores use the rolling technical score "
                    "and today's latest persisted non-price factor scores, then "
                    "measure forward 5D, 15D and 30D returns."
                ),
                "guardrail": (
                    "Hit rate is the share of matched sessions with a positive "
                    "forward return for that horizon."
                ),
            },
            "disclaimer": (
                "Quantitative research signal only. It is not investment advice, "
                "a recommendation, or a guarantee of returns."
            ),
        }
        ready = json_ready(result)
        if self.cache is not None:
            self.cache.set(cache_key, ready, self.deep_dive_cache_seconds)
        return ready

    @staticmethod
    def _safe(operation, errors: list[str], label: str):
        try:
            return operation()
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            return None


def _indication(
    *,
    weekly_indicator: dict[str, Any] | None,
    monthly_indicator: dict[str, Any] | None,
    entry: dict[str, Any] | None,
    regime: dict[str, Any] | None,
) -> dict[str, Any]:
    weekly_signal = str((weekly_indicator or {}).get("signal", "HOLD"))
    monthly_signal = str((monthly_indicator or {}).get("signal", "HOLD"))
    entry_ready = bool((entry or {}).get("entry_ready"))
    risk_off = (regime or {}).get("state") == "RISK_OFF"
    if weekly_signal == "SELL" or monthly_signal == "SELL":
        signal = "SELL"
        summary = "Bearish completed-candle crossover detected."
    elif not risk_off and entry_ready and weekly_signal == "BUY":
        signal = "BUY"
        summary = "Weekly crossover and entry-risk controls are aligned."
    elif weekly_signal == "BUY" or monthly_signal == "BUY":
        signal = "WATCH"
        summary = "Bullish crossover exists, but all entry controls are not aligned."
    else:
        signal = "HOLD"
        summary = "No complete buy or sell setup is active."
    if risk_off and signal in {"BUY", "WATCH"}:
        signal = "AVOID"
        summary = "Market regime is risk-off; new entry is blocked."
    return {
        "signal": signal,
        "summary": summary,
        "weekly_signal": weekly_signal,
        "monthly_signal": monthly_signal,
        "entry_ready": entry_ready,
        "risk_off": risk_off,
    }


def _latest_alpha_features(snapshots: Any, symbol: str) -> dict[str, Any]:
    loader = getattr(snapshots, "latest_alpha_features", None)
    if not loader:
        return {}
    return dict(loader([symbol]).get(symbol, {}))


def _normalize_horizon(value: str) -> str:
    allowed = {"5d", "15d", "30d"}
    normalized = str(value or "15d").strip().lower()
    if normalized not in allowed:
        raise ValueError("horizon must be one of: 5d, 15d, 30d")
    return normalized


def _technical_breakdown(frame: pd.DataFrame) -> dict[str, Any]:
    data = _indicator_frame(frame)
    latest = data.iloc[-1]
    previous = data.iloc[-2] if len(data) > 1 else latest
    price = float(latest["Close"])
    sma20 = float(latest["sma20"])
    adx = float(latest["adx_14"])
    atr_ratio = float(latest["atr_14"] / latest["avg_atr_50"]) if latest["avg_atr_50"] else 1.0
    price_vs_sma = (price / sma20 - 1) * 100 if sma20 else 0.0
    macd_slope = float(latest["macd_histogram"] - data["macd_histogram"].iloc[-4]) if len(data) >= 4 else 0.0
    flags = _technical_flags(latest, previous, macd_slope, atr_ratio)
    return {
        "weight": 0.35,
        "score": round(float(latest["technical_score"]), 2),
        "coverage": "FULL",
        "as_of": data.index[-1].date().isoformat(),
        "price": round(price, 2),
        "trend": {
            "ema_20": round(float(latest["ema20"]), 2),
            "ema_50": round(float(latest["ema50"]), 2),
            "ema_200": round(float(latest["ema200"]), 2),
            "ema_alignment": _ema_alignment(latest),
            "adx_14": round(adx, 2),
            "display": (
                f"EMA 20/50/200: {_ema_alignment(latest).replace('_', ' ').title()} "
                f"| ADX: {adx:.1f} ({'Trending' if adx >= 25 else 'Weak trend'})"
            ),
        },
        "momentum": {
            "rsi_14": round(float(latest["rsi_14"]), 2),
            "macd": round(float(latest["macd"]), 4),
            "macd_signal": round(float(latest["macd_signal"]), 4),
            "macd_histogram": round(float(latest["macd_histogram"]), 4),
            "macd_histogram_slope": round(macd_slope, 4),
            "display": (
                f"RSI: {float(latest['rsi_14']):.1f} | MACD histogram slope: "
                f"{macd_slope:+.4f}"
            ),
        },
        "volatility": {
            "atr_14": round(float(latest["atr_14"]), 4),
            "average_atr_50": round(float(latest["avg_atr_50"]), 4),
            "atr_vs_average": round((atr_ratio - 1) * 100, 2),
            "display": (
                f"ATR vs 50D avg: {(atr_ratio - 1) * 100:+.1f}% "
                f"({'Expanded' if atr_ratio > 1.15 else 'Compressed' if atr_ratio < 0.85 else 'Normal'})"
            ),
        },
        "price_vs_sma20": {
            "value_pct": round(price_vs_sma, 2),
            "display": (
                f"Price vs SMA20: {price_vs_sma:+.1f}% "
                f"({'Bullish' if price_vs_sma >= 0 else 'Bearish'})"
            ),
        },
        "condition_flags": flags,
    }


def _indicator_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    if "Close" not in data:
        raise ValueError("OHLCV data missing Close column")
    for name in ("Open", "High", "Low"):
        if name not in data:
            data[name] = data["Close"]
    if "Volume" not in data:
        data["Volume"] = 0
    data = data.loc[:, ["Open", "High", "Low", "Close", "Volume"]].copy()
    data.index = pd.to_datetime(data.index)
    data = data.apply(pd.to_numeric, errors="coerce").dropna().sort_index()
    if len(data) < 230:
        raise ValueError("At least 230 complete sessions are required for deep analysis")
    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    volume = data["Volume"].astype(float)
    data["ema20"] = close.ewm(span=20, adjust=False).mean()
    data["ema50"] = close.ewm(span=50, adjust=False).mean()
    data["ema200"] = close.ewm(span=200, adjust=False).mean()
    data["sma20"] = close.rolling(20).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    data["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-12))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    data["macd"] = ema12 - ema26
    data["macd_signal"] = data["macd"].ewm(span=9, adjust=False).mean()
    data["macd_histogram"] = data["macd"] - data["macd_signal"]
    data["atr_14"] = _atr(data, 14)
    data["avg_atr_50"] = data["atr_14"].rolling(50).mean()
    plus_di, negative_di, adx = _adx(high, low, close)
    data["positive_di_14"] = plus_di
    data["negative_di_14"] = negative_di
    data["adx_14"] = adx
    data["momentum_20d"] = close.pct_change(20) * 100
    middle = data["sma20"]
    deviation = close.rolling(20).std()
    band_width = (middle + 2 * deviation) - (middle - 2 * deviation)
    data["band_position"] = (close - (middle - 2 * deviation)) / band_width.replace(0, np.nan)
    data["volume_ratio"] = volume / volume.rolling(20).mean().replace(0, np.nan)
    data["technical_score"] = _technical_score_series(data)
    return data.dropna(subset=["technical_score", "sma20", "atr_14", "avg_atr_50", "adx_14"])


def _atr(data: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = data["High"], data["Low"], data["Close"]
    true_range = pd.concat(
        (high - low, (high - close.shift()).abs(), (low - close.shift()).abs()),
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    negative_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    true_range = pd.concat(
        (high - low, (high - close.shift()).abs(), (low - close.shift()).abs()),
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    negative_di = 100 * negative_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    dx = ((plus_di - negative_di).abs() / (plus_di + negative_di).replace(0, np.nan)) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return plus_di, negative_di, adx


def _technical_score_series(data: pd.DataFrame) -> pd.Series:
    close = data["Close"]
    score = pd.Series(0.0, index=data.index)
    score += np.where(close > data["ema200"], 15, -15)
    score += np.where(data["ema20"] > data["ema50"], 15, -15)
    score += np.where(data["ema50"] > data["ema200"], 10, -10)
    score += np.where(data["macd"] > data["macd_signal"], 20, -20)
    rsi = data["rsi_14"]
    score += np.select(
        [rsi.between(50, 70), (rsi >= 30) & (rsi < 50), rsi < 30, rsi > 70],
        [20, -10, -20, 5],
        default=0,
    )
    momentum = data["momentum_20d"]
    score += np.where(momentum > 3, 15, np.where(momentum < -3, -15, 0))
    score += np.where(data["band_position"] >= 0.6, 5, np.where(data["band_position"] <= 0.4, -5, 0))
    score += np.where(data["volume_ratio"] >= 1.2, np.where(score >= 0, 5, -5), 0)
    return pd.Series(np.clip((score + 100) / 2, 0, 100), index=data.index)


def _technical_flags(latest: pd.Series, previous: pd.Series, macd_slope: float, atr_ratio: float) -> list[str]:
    flags: list[str] = []
    if latest["ema20"] > latest["ema50"] > latest["ema200"]:
        flags.append("EMA_BULLISH_ALIGNMENT")
    if latest["ema20"] < latest["ema50"] < latest["ema200"]:
        flags.append("EMA_BEARISH_ALIGNMENT")
    if latest["macd"] > latest["macd_signal"] and previous["macd"] <= previous["macd_signal"]:
        flags.append("MACD_BULLISH_CROSS")
    if latest["macd"] < latest["macd_signal"] and previous["macd"] >= previous["macd_signal"]:
        flags.append("MACD_BEARISH_CROSS")
    if latest["macd"] > latest["macd_signal"]:
        flags.append("MACD_BULLISH")
    if macd_slope > 0:
        flags.append("MACD_HISTOGRAM_RISING")
    if latest["rsi_14"] >= 70:
        flags.append("RSI_OVERBOUGHT")
    elif latest["rsi_14"] <= 30:
        flags.append("RSI_OVERSOLD")
    elif latest["rsi_14"] >= 50:
        flags.append("RSI_BULLISH_REGIME")
    if latest["adx_14"] >= 25:
        flags.append("ADX_TRENDING")
    if atr_ratio >= 1.15:
        flags.append("ATR_EXPANSION")
    return flags


def _ema_alignment(row: pd.Series) -> str:
    if row["ema20"] > row["ema50"] > row["ema200"]:
        return "BULLISH_ALIGNMENT"
    if row["ema20"] < row["ema50"] < row["ema200"]:
        return "BEARISH_ALIGNMENT"
    return "MIXED_ALIGNMENT"


def _fundamental_breakdown(raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    features = dict(item.get("features", {}))
    score = _number(item.get("score"))
    roe = _number(features.get("roe_pct"))
    sector_percentile = _number(features.get("sector_roe_percentile"))
    return {
        "weight": 0.30,
        "score": round(score if score is not None else 50.0, 2),
        "coverage": str(item.get("coverage", "MISSING")) if item else "MISSING",
        "as_of": item.get("as_of"),
        "valuation": {
            "pe_ratio": _round_or_none(features.get("pe_ratio")),
            "sector_pe_ratio": _round_or_none(features.get("sector_pe_ratio")),
            "pb_ratio": _round_or_none(features.get("pb_ratio", features.get("price_to_book"))),
            "display": _valuation_display(features),
        },
        "quality": {
            "roe_pct": _round_or_none(roe),
            "roce_pct": _round_or_none(features.get("roce_pct")),
            "debt_to_equity": _round_or_none(features.get("debt_to_equity")),
            "display": _roe_display(roe, sector_percentile),
        },
        "growth": {
            "ttm_revenue_growth_pct": _round_or_none(features.get("ttm_revenue_growth_pct")),
            "qoq_profit_growth_pct": _round_or_none(
                features.get("qoq_profit_growth_pct", features.get("ttm_net_profit_growth_pct"))
            ),
            "fcf_growth_pct": _round_or_none(features.get("fcf_growth_pct")),
            "display": _growth_display(features),
        },
        "contributions": dict(item.get("contributions", {})),
    }


def _smart_money_breakdown(raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    features = dict(item.get("features", {}))
    score = _number(item.get("score"))
    pcr = _number(_first_present(features, "pcr", "put_call_ratio"))
    return {
        "weight": 0.25,
        "score": round(score if score is not None else 50.0, 2),
        "coverage": str(item.get("coverage", "MISSING")) if item else "MISSING",
        "as_of": item.get("as_of"),
        "oi_change": _round_or_none(_first_present(features, "oi_change_pct", "open_interest_change_pct")),
        "pcr": _round_or_none(pcr),
        "iv_skew": _round_or_none(_first_present(features, "iv_skew", "implied_volatility_skew")),
        "gex": _round_or_none(_first_present(features, "gex", "gamma_exposure")),
        "display": _pcr_display(pcr),
        "contributions": dict(item.get("contributions", {})),
    }


def _sentiment_legal_breakdown(sentiment_raw: Any, legal_raw: Any) -> dict[str, Any]:
    sentiment = sentiment_raw if isinstance(sentiment_raw, dict) else {}
    legal = legal_raw if isinstance(legal_raw, dict) else {}
    sentiment_score = _number(sentiment.get("score"))
    composite = _number(sentiment.get("composite_score"))
    features = dict(sentiment.get("features", {}))
    if composite is None:
        composite = _number(features.get("composite_score"))
    if composite is None and sentiment_score is not None:
        composite = sentiment_score / 50 - 1
    risk = _number(legal.get("risk_quotient"))
    legal_flag = str(legal.get("risk_flag") or _legal_flag(risk))
    sentiment_component = sentiment_score if sentiment_score is not None else 50.0
    legal_component = 100.0 - (risk if risk is not None else 50.0)
    score = max(0.0, min(100.0, 0.60 * sentiment_component + 0.40 * legal_component))
    return {
        "weight": 0.10,
        "score": round(score, 2),
        "coverage": (
            "FULL"
            if sentiment and legal
            else "PARTIAL"
            if sentiment or legal
            else "MISSING"
        ),
        "sentiment_score": round(float(composite), 4) if composite is not None else None,
        "finbert_score": _round_or_none(sentiment_score),
        "legal_risk": legal_flag.upper(),
        "legal_risk_quotient": _round_or_none(risk),
        "display": (
            f"News Sentiment: {_signed(composite)} ({_sentiment_label(composite)}) | "
            f"Legal Risk: {legal_flag.upper()}"
        ),
        "sentiment_contributions": dict(sentiment.get("contributions", {})),
        "legal_contributions": dict(legal.get("contributions", {})),
    }


def _weighted_score(scores: dict[str, float]) -> dict[str, Any]:
    weights = {
        "technical": 0.35,
        "fundamental": 0.30,
        "smart_money": 0.25,
        "sentiment_legal": 0.10,
    }
    contributions = {
        name: round(float(scores[name]) * weight, 2)
        for name, weight in weights.items()
    }
    return {
        "overall_score": round(sum(contributions.values()), 2),
        "contributions": contributions,
    }


def _projected_returns(
    frame: pd.DataFrame, current_score: float, current_factor_scores: dict[str, float]
) -> dict[str, Any]:
    data = _indicator_frame(frame)
    cutoff = data.index[-1] - pd.DateOffset(years=5)
    data = data[data.index >= cutoff]
    candidates = data.iloc[:-30].copy()
    candidates["composite_score"] = (
        0.35 * candidates["technical_score"]
        + 0.30 * float(current_factor_scores["fundamental"])
        + 0.25 * float(current_factor_scores["smart_money"])
        + 0.10 * float(current_factor_scores["sentiment_legal"])
    )
    matched = candidates[(candidates["composite_score"] - current_score).abs() <= 5].tail(50)
    warnings: list[str] = []
    if len(matched) < 20:
        warnings.append(
            f"Projection sample is thin: {len(matched)} historical matches found within +/-5 score points."
        )
    result: dict[str, Any] = {
        "method": "historical_percentile_matching",
        "match_tolerance": 5,
        "sample_size": int(len(matched)),
        "score_matched": round(float(current_score), 2),
        "warnings": warnings,
    }
    close = data["Close"]
    for days in (5, 15, 30):
        returns = ((close.shift(-days) / close - 1) * 100).reindex(matched.index).dropna()
        key = f"horizon_{days}d"
        if returns.empty:
            result[key] = {"median": None, "lower": None, "upper": None, "hit_rate": None}
            continue
        result[key] = {
            "median": round(float(np.percentile(returns, 50)), 2),
            "lower": round(float(np.percentile(returns, 25)), 2),
            "upper": round(float(np.percentile(returns, 75)), 2),
            "hit_rate": round(float((returns > 0).mean() * 100), 1),
        }
    return result


def _overall_signal(score: float) -> str:
    if score >= 80:
        return "STRONG_BUY"
    if score >= 65:
        return "BUY"
    if score >= 45:
        return "HOLD"
    if score >= 30:
        return "SELL"
    return "STRONG_SELL"


def _confidence_interval(values: list[float | None]) -> str:
    available = [float(value) for value in values if value is not None]
    if len(available) < 2:
        return "Low"
    dispersion = float(np.std(available))
    same_side = all(value >= 55 for value in available) or all(value <= 45 for value in available)
    if len(available) >= 4 and dispersion <= 15 and same_side:
        return "High"
    if len(available) >= 3 and dispersion <= 25:
        return "Medium"
    return "Low"


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _round_or_none(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _first_present(values: dict[str, Any], *names: str) -> Any:
    for name in names:
        if values.get(name) is not None:
            return values[name]
    return None


def _valuation_display(features: dict[str, Any]) -> str:
    pe = _number(features.get("pe_ratio"))
    sector_pe = _number(features.get("sector_pe_ratio"))
    if pe is None or sector_pe is None or sector_pe <= 0:
        return "PE vs Sector PE: unavailable"
    premium = (pe / sector_pe - 1) * 100
    label = "Discount" if premium < -10 else "Premium" if premium > 10 else "In line"
    return f"PE: {pe:.1f} vs Sector PE: {sector_pe:.1f} ({premium:+.1f}%, {label})"


def _roe_display(roe: float | None, sector_percentile: float | None) -> str:
    if roe is None:
        return "ROE: unavailable"
    if sector_percentile is not None:
        top_bucket = max(0, min(100, 100 - sector_percentile))
        return f"ROE: {roe:.1f}% (Top {top_bucket:.0f}% of sector)"
    label = "Strong" if roe >= 18 else "Adequate" if roe >= 12 else "Weak"
    return f"ROE: {roe:.1f}% ({label})"


def _growth_display(features: dict[str, Any]) -> str:
    revenue = _number(features.get("ttm_revenue_growth_pct"))
    profit = _number(features.get("qoq_profit_growth_pct", features.get("ttm_net_profit_growth_pct")))
    if revenue is None and profit is None:
        return "Growth: unavailable"
    return f"Revenue growth: {_signed(revenue)} | Profit growth: {_signed(profit)}"


def _pcr_display(pcr: float | None) -> str:
    if pcr is None:
        return "PCR: unavailable"
    if pcr >= 1.1:
        label = "Strong put writing, Bullish"
    elif pcr <= 0.8:
        label = "Call writing pressure, Bearish"
    else:
        label = "Balanced"
    return f"PCR: {pcr:.2f} ({label})"


def _legal_flag(risk: float | None) -> str:
    if risk is None:
        return "Unknown"
    return "High" if risk >= 70 else "Medium" if risk >= 35 else "Low"


def _sentiment_label(score: float | None) -> str:
    if score is None:
        return "Unavailable"
    if score >= 0.6:
        return "Very Positive"
    if score >= 0.2:
        return "Positive"
    if score <= -0.6:
        return "Very Negative"
    if score <= -0.2:
        return "Negative"
    return "Neutral"


def _signed(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:+.1f}"
