from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from nse_dashboard.core.json import json_ready
from nse_dashboard.infrastructure.cache import TtlCache
from sector_map import get_sector

FEATURE_CACHE_TTL_SECONDS = 4 * 60 * 60
FEATURE_SET_VERSION = "ml-features-v1"


def normalize_nse_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if value.endswith(".NS") or value.endswith(".BO"):
        return value
    return f"{value}.NS"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _pct_change(close: pd.Series, sessions: int) -> float:
    if len(close) <= sessions:
        return 0.0
    base = _finite(close.iloc[-sessions - 1])
    latest = _finite(close.iloc[-1])
    if base <= 0:
        return 0.0
    return (latest / base - 1) * 100


def _max_drawdown(close: pd.Series) -> float:
    if close.empty:
        return 0.0
    running_high = close.cummax()
    drawdown = close / running_high.where(running_high != 0, 1e-12) - 1
    return _finite(drawdown.min() * 100)


def _flatten_yfinance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy()
        frame.columns = [str(column[0]) for column in frame.columns]
    return frame


class MLFeatureService:
    def __init__(self, cache: TtlCache | None = None, snapshots: Any | None = None) -> None:
        self.cache = cache
        self.snapshots = snapshots

    def build_features(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_nse_symbol(symbol)
        cache_key = f"features:{normalized.removesuffix('.NS')}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        features = self._build_uncached(normalized)
        payload = json_ready(features)
        if self.cache is not None:
            self.cache.set(cache_key, payload, FEATURE_CACHE_TTL_SECONDS)
        return payload

    def _build_uncached(self, symbol: str) -> dict[str, Any]:
        import yfinance as yf

        history = _flatten_yfinance_frame(
            yf.download(symbol, period="5y", interval="1d", progress=False, auto_adjust=False)
        )
        if history.empty or "Close" not in history:
            raise ValueError(f"No Yahoo Finance daily history for {symbol}")

        close = history["Close"].dropna().astype(float)
        volume = history.get("Volume", pd.Series(dtype=float)).reindex(close.index).fillna(0).astype(float)
        high = history.get("High", close).reindex(close.index).fillna(close).astype(float)
        low = history.get("Low", close).reindex(close.index).fillna(close).astype(float)
        returns = close.pct_change()

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        financials = getattr(ticker, "financials", pd.DataFrame())
        balance_sheet = getattr(ticker, "balance_sheet", pd.DataFrame())
        cashflow = getattr(ticker, "cashflow", pd.DataFrame())
        fundamentals = self._fundamental_features(info, financials, balance_sheet, cashflow)
        alpha_hooks = self._alpha_hooks(symbol)

        sma_50 = close.rolling(50).mean()
        sma_100 = close.rolling(100).mean()
        sma_200 = close.rolling(200).mean()
        ema_20 = close.ewm(span=20, adjust=False).mean()
        ema_50 = close.ewm(span=50, adjust=False).mean()
        volatility_20d = _finite(returns.rolling(20).std().iloc[-1] * math.sqrt(252) * 100)
        volatility_60d = _finite(returns.rolling(60).std().iloc[-1] * math.sqrt(252) * 100)
        latest_price = _finite(close.iloc[-1])
        average_value = _finite((close * volume).tail(20).mean())
        true_range = pd.concat(
            [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr_14 = _finite(true_range.rolling(14).mean().iloc[-1])

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - (100 / (1 + gain / loss.where(loss != 0, 1e-12)))

        market_cap = _finite(info.get("marketCap"))
        payload: dict[str, Any] = {
            "symbol": symbol,
            "name": info.get("shortName") or info.get("longName") or symbol.removesuffix(".NS"),
            "sector": info.get("sector") or get_sector(symbol),
            "industry": info.get("industry"),
            "as_of": close.index[-1].date().isoformat() if hasattr(close.index[-1], "date") else str(close.index[-1]),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "feature_set_version": FEATURE_SET_VERSION,
            "current_price": round(latest_price, 2),
            "features": {
                "return_1m_pct": _pct_change(close, 21),
                "return_3m_pct": _pct_change(close, 63),
                "return_6m_pct": _pct_change(close, 126),
                "return_12m_pct": _pct_change(close, 252),
                "return_24m_pct": _pct_change(close, 504),
                "volatility_20d_pct": volatility_20d,
                "volatility_60d_pct": volatility_60d,
                "max_drawdown_1y_pct": _max_drawdown(close.tail(252)),
                "max_drawdown_5y_pct": _max_drawdown(close),
                "price_vs_50dma_pct": _finite((latest_price / sma_50.iloc[-1] - 1) * 100),
                "price_vs_100dma_pct": _finite((latest_price / sma_100.iloc[-1] - 1) * 100),
                "price_vs_200dma_pct": _finite((latest_price / sma_200.iloc[-1] - 1) * 100),
                "ema_20_50_spread_pct": _finite((ema_20.iloc[-1] / ema_50.iloc[-1] - 1) * 100),
                "rsi_14": _finite(rsi.iloc[-1], 50.0),
                "atr_14_pct": _finite(atr_14 / latest_price * 100),
                "volume_20d_avg": _finite(volume.tail(20).mean()),
                "volume_60d_avg": _finite(volume.tail(60).mean()),
                "volume_ratio_20d": _finite(volume.iloc[-1] / max(volume.tail(20).mean(), 1.0)),
                "average_traded_value_20d": average_value,
                "market_cap_cr": market_cap / 10_000_000 if market_cap else 0.0,
                **fundamentals,
                **alpha_hooks,
            },
        }
        return payload

    def _fundamental_features(
        self,
        info: dict[str, Any],
        financials: pd.DataFrame,
        balance_sheet: pd.DataFrame,
        cashflow: pd.DataFrame,
    ) -> dict[str, float]:
        revenue = self._statement_row(financials, "Total Revenue")
        ebit = self._statement_row(financials, "EBIT")
        total_assets = self._statement_row(balance_sheet, "Total Assets")
        current_liabilities = self._statement_row(balance_sheet, "Current Liabilities")
        free_cash_flow = self._statement_row(cashflow, "Free Cash Flow")
        market_cap = _finite(info.get("marketCap"))
        capital_employed = max(total_assets - current_liabilities, 1.0)
        latest_revenue, prior_revenue = self._latest_and_prior(financials, "Total Revenue")
        revenue_growth = (latest_revenue / prior_revenue - 1) * 100 if prior_revenue > 0 else 0.0

        pe = _finite(info.get("trailingPE") or info.get("forwardPE"))
        sector_pe = _finite(info.get("sectorPE") or pe)
        return {
            "roe_pct": _finite((info.get("returnOnEquity") or 0) * 100),
            "roce_pct": _finite(ebit / capital_employed * 100),
            "debt_to_equity": _finite(info.get("debtToEquity")) / 100,
            "revenue_growth_ttm_pct": _finite(revenue_growth),
            "free_cash_flow_yield_pct": _finite(free_cash_flow / market_cap * 100) if market_cap else 0.0,
            "pe_ratio": pe,
            "sector_pe_ratio": sector_pe,
            "pe_vs_sector_pct": _finite((pe / sector_pe - 1) * 100) if sector_pe else 0.0,
            "pb_ratio": _finite(info.get("priceToBook")),
            "profit_margin_pct": _finite((info.get("profitMargins") or 0) * 100),
            "operating_margin_pct": _finite((info.get("operatingMargins") or 0) * 100),
            "gross_margin_pct": _finite((info.get("grossMargins") or 0) * 100),
            "revenue_per_share": _finite(info.get("revenuePerShare")),
            "earnings_growth_pct": _finite((info.get("earningsGrowth") or 0) * 100),
            "current_ratio": _finite(info.get("currentRatio")),
            "quick_ratio": _finite(info.get("quickRatio")),
            "beta": _finite(info.get("beta"), 1.0),
            "dividend_yield_pct": _finite((info.get("dividendYield") or 0) * 100),
            "total_revenue": revenue,
        }

    def _statement_row(self, statement: pd.DataFrame, name: str) -> float:
        if statement.empty or name not in statement.index or statement.loc[name].empty:
            return 0.0
        return _finite(statement.loc[name].dropna().iloc[0] if not statement.loc[name].dropna().empty else 0)

    def _latest_and_prior(self, statement: pd.DataFrame, name: str) -> tuple[float, float]:
        if statement.empty or name not in statement.index:
            return 0.0, 0.0
        values = statement.loc[name].dropna()
        if len(values) < 2:
            return _finite(values.iloc[0] if len(values) else 0), 0.0
        return _finite(values.iloc[0]), _finite(values.iloc[1])

    def _alpha_hooks(self, symbol: str) -> dict[str, float]:
        defaults = {
            "smart_money_score": 0.0,
            "pcr": 0.0,
            "oi_change_pct": 0.0,
            "gex": 0.0,
            "finbert_sentiment_score": 0.0,
            "legal_risk_quotient": 0.0,
        }
        if self.snapshots is None:
            return defaults
        try:
            latest = self.snapshots.latest_alpha_features([symbol]).get(symbol, {})
        except Exception:
            return defaults
        options = latest.get("options", {})
        sentiment = latest.get("sentiment", {})
        legal = latest.get("legal", {})
        option_features = options.get("features", {})
        sentiment_features = sentiment.get("features", {})
        return {
            "smart_money_score": _finite(options.get("score")),
            "pcr": _finite(option_features.get("pcr")),
            "oi_change_pct": _finite(option_features.get("oi_change_pct")),
            "gex": _finite(option_features.get("gex")),
            "finbert_sentiment_score": _finite(
                sentiment.get("composite_score", sentiment_features.get("finbert_score"))
            ),
            "legal_risk_quotient": _finite(legal.get("risk_quotient")),
        }


def build_features(symbol: str) -> dict[str, Any]:
    return MLFeatureService().build_features(symbol)
