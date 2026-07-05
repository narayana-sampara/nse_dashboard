from __future__ import annotations

import math
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from nse_dashboard.core.json import json_ready
from nse_dashboard.infrastructure.cache import TtlCache
from nse_dashboard.services.ml_feature_service import (
    FEATURE_SET_VERSION,
    MLFeatureService,
    _finite,
    _flatten_yfinance_frame,
)
from sector_map import SECTOR_MAP, display_name

FORECAST_CACHE_KEY = "ml:forecast:v1"
FORECAST_CACHE_TTL_SECONDS = 2 * 60 * 60
MODEL_VERSION = "v1.2.3"
MODEL_NAME = "lightgbm_forward_1y"
DEFAULT_MODEL_DIR = Path("artifacts") / "ml"
FEATURE_COLUMNS = [
    "return_1m_pct",
    "return_3m_pct",
    "return_6m_pct",
    "return_12m_pct",
    "return_24m_pct",
    "volatility_20d_pct",
    "volatility_60d_pct",
    "max_drawdown_1y_pct",
    "max_drawdown_5y_pct",
    "price_vs_50dma_pct",
    "price_vs_100dma_pct",
    "price_vs_200dma_pct",
    "ema_20_50_spread_pct",
    "rsi_14",
    "atr_14_pct",
    "volume_ratio_20d",
    "average_traded_value_20d",
    "market_cap_cr",
    "roe_pct",
    "roce_pct",
    "debt_to_equity",
    "revenue_growth_ttm_pct",
    "free_cash_flow_yield_pct",
    "pe_ratio",
    "sector_pe_ratio",
    "pe_vs_sector_pct",
    "pb_ratio",
    "profit_margin_pct",
    "operating_margin_pct",
    "gross_margin_pct",
    "revenue_per_share",
    "earnings_growth_pct",
    "current_ratio",
    "quick_ratio",
    "beta",
    "dividend_yield_pct",
    "smart_money_score",
    "pcr",
    "oi_change_pct",
    "gex",
    "finbert_sentiment_score",
    "legal_risk_quotient",
]


@dataclass(frozen=True, slots=True)
class PurgedSplit:
    train_index: list[int]
    test_index: list[int]


class PurgedTimeSeriesSplit:
    def __init__(self, n_splits: int = 5, embargo_days: int = 30) -> None:
        self.n_splits = n_splits
        self.embargo_days = embargo_days

    def split(self, frame: pd.DataFrame, date_column: str = "as_of") -> Iterable[PurgedSplit]:
        ordered = frame.sort_values(date_column).reset_index(drop=True)
        dates = pd.to_datetime(ordered[date_column])
        unique_dates = pd.Series(dates.drop_duplicates().sort_values().to_list())
        if len(unique_dates) < self.n_splits + 1:
            return
        fold_size = max(1, len(unique_dates) // (self.n_splits + 1))
        for fold in range(1, self.n_splits + 1):
            test_start = fold * fold_size
            test_end = min(len(unique_dates), test_start + fold_size)
            test_dates = unique_dates.iloc[test_start:test_end]
            if test_dates.empty:
                continue
            embargo_start = test_dates.iloc[0] - pd.Timedelta(days=self.embargo_days)
            train_index = ordered.index[dates < embargo_start].to_list()
            test_index = ordered.index[
                (dates >= test_dates.iloc[0]) & (dates <= test_dates.iloc[-1])
            ].to_list()
            if train_index and test_index:
                yield PurgedSplit(train_index=train_index, test_index=test_index)


class HeuristicForwardReturnModel:
    """Deterministic fallback used until LightGBM is installed and trained."""

    feature_importance_ = {
        "return_12m_pct": 0.18,
        "return_6m_pct": 0.14,
        "price_vs_200dma_pct": 0.11,
        "roe_pct": 0.10,
        "roce_pct": 0.10,
        "revenue_growth_ttm_pct": 0.09,
        "free_cash_flow_yield_pct": 0.08,
        "debt_to_equity": -0.07,
        "volatility_20d_pct": -0.06,
        "pe_vs_sector_pct": -0.04,
        "finbert_sentiment_score": 0.03,
    }

    def predict(self, frame: pd.DataFrame) -> list[float]:
        rows: list[float] = []
        for _, row in frame.iterrows():
            score = 8.0
            score += min(40, max(-20, _finite(row.get("return_12m_pct")))) * 0.22
            score += min(35, max(-20, _finite(row.get("return_6m_pct")))) * 0.18
            score += min(30, max(-30, _finite(row.get("price_vs_200dma_pct")))) * 0.12
            score += min(35, max(0, _finite(row.get("roe_pct")))) * 0.28
            score += min(35, max(0, _finite(row.get("roce_pct")))) * 0.22
            score += min(50, max(-30, _finite(row.get("revenue_growth_ttm_pct")))) * 0.12
            score += min(15, max(-10, _finite(row.get("free_cash_flow_yield_pct")))) * 0.35
            score -= min(5, max(0, _finite(row.get("debt_to_equity")))) * 4.5
            score -= max(0, _finite(row.get("volatility_20d_pct")) - 35) * 0.18
            score -= max(0, _finite(row.get("pe_vs_sector_pct"))) * 0.04
            score += _finite(row.get("smart_money_score")) * 0.04
            score += _finite(row.get("finbert_sentiment_score")) * 2.0
            rows.append(max(-60.0, min(180.0, score)))
        return rows


class MLForecastService:
    def __init__(
        self,
        cache: TtlCache | None = None,
        snapshots: Any | None = None,
        model_dir: Path | str = DEFAULT_MODEL_DIR,
        feature_service: MLFeatureService | None = None,
    ) -> None:
        self.cache = cache
        self.snapshots = snapshots
        self.model_dir = Path(model_dir)
        self.feature_service = feature_service or MLFeatureService(cache=cache, snapshots=snapshots)

    @property
    def model_path(self) -> Path:
        return self.model_dir / f"{MODEL_NAME}_{MODEL_VERSION}.pkl"

    def latest(self, limit: int = 20) -> dict[str, Any]:
        cached = self.cache.get(FORECAST_CACHE_KEY) if self.cache is not None else None
        if cached is not None:
            return {**cached, "predictions": cached.get("predictions", [])[:limit]}
        if self.snapshots is not None:
            getter = getattr(self.snapshots, "latest_ml_predictions", None)
            if getter is not None:
                snapshot = getter(limit)
                if snapshot is not None:
                    if self.cache is not None:
                        self.cache.set(FORECAST_CACHE_KEY, snapshot, FORECAST_CACHE_TTL_SECONDS)
                    return snapshot
        return {
            "generated_at": None,
            "model_version": MODEL_VERSION,
            "predictions": [],
            "generation_status": {
                "state": "missing",
                "message": "No ML forecast has been generated yet.",
            },
        }

    def infer(self, symbols: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        symbols = symbols or list(SECTOR_MAP)
        model = self._load_model()
        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for symbol in symbols:
            try:
                payload = self.feature_service.build_features(symbol)
                feature_row = self._feature_row(payload["features"])
                prediction_pct = _finite(model.predict(pd.DataFrame([feature_row]))[0])
                shap_values = self._explain(model, feature_row)
                rows.append(self._prediction_payload(payload, prediction_pct, shap_values))
            except Exception:
                failures.append(symbol)

        rows.sort(
            key=lambda item: (
                -float(item["implied_cagr_pct"]),
                -float(item["probability_positive"]),
                str(item["symbol"]),
            )
        )
        for rank, item in enumerate(rows, start=1):
            item["rank"] = rank
        snapshot = json_ready(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model_version": MODEL_VERSION,
                "model": {
                    "name": MODEL_NAME,
                    "version": MODEL_VERSION,
                    "feature_set_version": FEATURE_SET_VERSION,
                    "fallback": isinstance(model, HeuristicForwardReturnModel),
                },
                "universe_size": len(symbols),
                "predictions_count": len(rows),
                "failures": failures,
                "predictions": rows[:limit],
                "disclaimer": "Forward return forecasts are research estimates, not guaranteed targets or investment advice.",
            }
        )
        if self.cache is not None:
            self.cache.set(FORECAST_CACHE_KEY, snapshot, FORECAST_CACHE_TTL_SECONDS)
        if self.snapshots is not None:
            saver = getattr(self.snapshots, "save_ml_predictions", None)
            if saver is not None:
                saver(snapshot)
        return snapshot

    def train(self, symbols: list[str] | None = None) -> dict[str, Any]:
        symbols = symbols or list(SECTOR_MAP)
        training = self._build_training_frame(symbols)
        if training.empty:
            raise RuntimeError("No ML training rows were produced")
        x = training[FEATURE_COLUMNS].fillna(0.0)
        y = training["forward_1y_return_pct"].astype(float)
        model = self._fit_lightgbm(x, y)
        validation = self._validate(model, training)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        with self.model_path.open("wb") as handle:
            pickle.dump(
                {
                    "model": model,
                    "trained_at": datetime.now(timezone.utc).isoformat(),
                    "feature_columns": FEATURE_COLUMNS,
                    "validation": validation,
                },
                handle,
            )
        return json_ready(
            {
                "model_name": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "training_rows": len(training),
                "symbols": len(symbols),
                "validation": validation,
            }
        )

    def _load_model(self) -> Any:
        if not self.model_path.exists():
            return HeuristicForwardReturnModel()
        with self.model_path.open("rb") as handle:
            artifact = pickle.load(handle)
        return artifact["model"]

    def _fit_lightgbm(self, x: pd.DataFrame, y: pd.Series) -> Any:
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:
            raise RuntimeError("Install lightgbm to train the ML forecast model") from exc
        model = LGBMRegressor(
            objective="regression",
            n_estimators=600,
            learning_rate=0.03,
            num_leaves=31,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=42,
        )
        model.fit(x, y)
        return model

    def _validate(self, model: Any, training: pd.DataFrame) -> dict[str, Any]:
        folds: list[dict[str, float | int]] = []
        for split in PurgedTimeSeriesSplit(n_splits=5, embargo_days=30).split(training):
            test = training.iloc[split.test_index]
            actual = test["forward_1y_return_pct"].astype(float)
            predicted = pd.Series(model.predict(test[FEATURE_COLUMNS].fillna(0.0)))
            error = predicted - actual.reset_index(drop=True)
            folds.append(
                {
                    "train_rows": len(split.train_index),
                    "test_rows": len(split.test_index),
                    "mae": round(float(error.abs().mean()), 4),
                    "rmse": round(float((error.pow(2).mean()) ** 0.5), 4),
                }
            )
        return {"method": "purged_time_series_split", "embargo_days": 30, "folds": folds}

    def _build_training_frame(self, symbols: list[str]) -> pd.DataFrame:
        import yfinance as yf

        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            history = _flatten_yfinance_frame(
                yf.download(symbol, start="2016-01-01", end="2026-01-01", progress=False, auto_adjust=False)
            )
            if history.empty or len(history) < 520 or "Close" not in history:
                continue
            history = history.dropna(subset=["Close"]).copy()
            close = history["Close"].astype(float)
            returns = close.pct_change()
            volume = history.get("Volume", pd.Series(dtype=float)).reindex(close.index).fillna(0).astype(float)
            rows = pd.DataFrame(index=history.index)
            rows["symbol"] = symbol
            rows["as_of"] = history.index
            rows["return_1m_pct"] = close.pct_change(21) * 100
            rows["return_3m_pct"] = close.pct_change(63) * 100
            rows["return_6m_pct"] = close.pct_change(126) * 100
            rows["return_12m_pct"] = close.pct_change(252) * 100
            rows["return_24m_pct"] = close.pct_change(504) * 100
            rows["volatility_20d_pct"] = returns.rolling(20).std() * math.sqrt(252) * 100
            rows["volatility_60d_pct"] = returns.rolling(60).std() * math.sqrt(252) * 100
            rows["max_drawdown_1y_pct"] = close.rolling(252).apply(
                lambda values: float((values / max(values.max(), 1e-12) - 1).min() * 100),
                raw=False,
            )
            rows["max_drawdown_5y_pct"] = close.rolling(1000, min_periods=252).apply(
                lambda values: float((values / max(values.max(), 1e-12) - 1).min() * 100),
                raw=False,
            )
            rows["price_vs_50dma_pct"] = (close / close.rolling(50).mean() - 1) * 100
            rows["price_vs_100dma_pct"] = (close / close.rolling(100).mean() - 1) * 100
            rows["price_vs_200dma_pct"] = (close / close.rolling(200).mean() - 1) * 100
            rows["ema_20_50_spread_pct"] = (
                close.ewm(span=20, adjust=False).mean() / close.ewm(span=50, adjust=False).mean() - 1
            ) * 100
            rows["rsi_14"] = self._rsi(close)
            true_range = pd.concat(
                [
                    history.get("High", close) - history.get("Low", close),
                    (history.get("High", close) - close.shift()).abs(),
                    (history.get("Low", close) - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            rows["atr_14_pct"] = true_range.rolling(14).mean() / close * 100
            rows["volume_ratio_20d"] = volume / volume.rolling(20).mean().where(volume.rolling(20).mean() != 0, 1)
            rows["average_traded_value_20d"] = (close * volume).rolling(20).mean()
            for column in FEATURE_COLUMNS:
                rows[column] = rows.get(column, 0.0)
            rows["forward_1y_return_pct"] = (close.shift(-252) / close - 1) * 100
            frames.append(rows.dropna(subset=["forward_1y_return_pct", "return_12m_pct", "price_vs_200dma_pct"]))
        if not frames:
            return pd.DataFrame()
        frame = pd.concat(frames, ignore_index=True)
        for column in FEATURE_COLUMNS:
            if column not in frame:
                frame[column] = 0.0
        return frame.fillna(0.0)

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        return 100 - (100 / (1 + gain / loss.where(loss != 0, 1e-12)))

    def _feature_row(self, features: dict[str, Any]) -> dict[str, float]:
        return {name: _finite(features.get(name)) for name in FEATURE_COLUMNS}

    def _explain(self, model: Any, row: dict[str, float]) -> dict[str, float]:
        frame = pd.DataFrame([row])
        try:
            import shap

            values = shap.TreeExplainer(model).shap_values(frame)
            first = values[0] if hasattr(values, "__len__") else values
            return {
                name: round(_finite(first[index]), 3)
                for index, name in enumerate(FEATURE_COLUMNS)
                if abs(_finite(first[index])) > 0
            }
        except Exception:
            weights = getattr(model, "feature_importance_", None)
            if isinstance(weights, dict):
                contributions = {name: _finite(row.get(name)) * weight for name, weight in weights.items()}
            else:
                mean_abs = sum(abs(_finite(value)) for value in row.values()) / max(len(row), 1)
                contributions = {name: _finite(value) / max(mean_abs, 1.0) for name, value in row.items()}
            return {
                name: round(value, 3)
                for name, value in sorted(
                    contributions.items(), key=lambda item: abs(item[1]), reverse=True
                )[:12]
            }

    def _prediction_payload(
        self, feature_payload: dict[str, Any], prediction_pct: float, shap_values: dict[str, float]
    ) -> dict[str, Any]:
        symbol = str(feature_payload["symbol"])
        current_price = _finite(feature_payload.get("current_price"))
        target_price = current_price * (1 + prediction_pct / 100)
        probability_positive = 1 / (1 + math.exp(-prediction_pct / 20))
        conviction = "HIGH" if probability_positive >= 0.75 else "MEDIUM" if probability_positive >= 0.58 else "SPECULATIVE"
        return {
            "symbol": symbol,
            "name": feature_payload.get("name") or display_name(symbol),
            "sector": feature_payload.get("sector") or SECTOR_MAP.get(symbol, "Unknown"),
            "current_price": round(current_price, 2),
            "target_price_1y": round(target_price, 2),
            "implied_cagr_pct": round(prediction_pct, 2),
            "probability_positive": round(probability_positive, 4),
            "conviction": conviction,
            "rank": 0,
            "shap_values": dict(
                sorted(shap_values.items(), key=lambda item: abs(item[1]), reverse=True)[:12]
            ),
            "dynamic_thesis": self._dynamic_thesis(shap_values),
        }

    def _dynamic_thesis(self, shap_values: dict[str, float]) -> str:
        positives = [
            name.replace("_", " ")
            for name, value in sorted(shap_values.items(), key=lambda item: item[1], reverse=True)
            if value > 0
        ][:3]
        negatives = [
            name.replace("_", " ")
            for name, value in sorted(shap_values.items(), key=lambda item: item[1])
            if value < 0
        ][:2]
        thesis = "Positive drivers: " + (", ".join(positives) if positives else "none dominant")
        if negatives:
            thesis += ". Key drags: " + ", ".join(negatives)
        return thesis + "."
