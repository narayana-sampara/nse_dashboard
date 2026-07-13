from __future__ import annotations

from typing import Any, Protocol


class SnapshotRepository(Protocol):
    def save_signal(self, snapshot: dict[str, Any]) -> None: ...

    def save_market_scan(
        self, snapshot: dict[str, Any], idempotency_key: str | None = None
    ) -> None: ...

    def save_market_quotes(self, snapshot: dict[str, Any]) -> int: ...

    def save_alerts(self, snapshot: dict[str, Any], idempotency_key: str) -> int: ...

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]: ...

    def signal_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]: ...

    def save_weekly_predictions(self, snapshot: dict[str, Any]) -> int: ...

    def latest_weekly_predictions(
        self, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]: ...

    def weekly_prediction_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def save_monthly_predictions(self, snapshot: dict[str, Any]) -> int: ...

    def latest_monthly_predictions(
        self, horizon_months: int, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]: ...

    def monthly_prediction_history(
        self, symbol: str, horizon_months: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def latest_alpha_features(
        self, symbols: list[str]
    ) -> dict[str, dict[str, Any]]: ...

    def save_alpha_ranking(self, snapshot: dict[str, Any]) -> int: ...

    def latest_alpha_ranking(
        self, horizon: str, horizon_months: int = 1
    ) -> dict[str, Any] | None: ...

    def save_ml_predictions(self, snapshot: dict[str, Any]) -> int: ...

    def latest_ml_predictions(self, limit: int = 20) -> dict[str, Any] | None: ...

    def save_growth_features(self, snapshot: dict[str, Any]) -> int: ...

    def save_filing_document(self, snapshot: dict[str, Any]) -> int: ...

    def latest_growth_features(
        self, symbols: list[str], known_at: str | None = None
    ) -> dict[str, dict[str, Any]]: ...

    def save_growth_radar(self, snapshot: dict[str, Any]) -> int: ...

    def latest_growth_radar(self) -> dict[str, Any] | None: ...

    def first_growth_signal(self, symbol: str) -> dict[str, Any] | None: ...

    def save_paper_portfolio(self, snapshot: dict[str, Any]) -> None: ...

    def latest_paper_portfolio(self) -> dict[str, Any] | None: ...

    def save_five_percent_strategy_run(self, snapshot: dict[str, Any]) -> int: ...

    def latest_five_percent_strategy_run(self) -> dict[str, Any]: ...

    def five_percent_strategy_run_by_id(self, run_id: str) -> dict[str, Any] | None: ...

    def five_percent_strategy_symbol_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def save_five_percent_backtest_run(self, snapshot: dict[str, Any]) -> int: ...

    def save_five_percent_paper_trade(self, trade: dict[str, Any]) -> dict[str, Any]: ...

    def list_five_percent_paper_trades(
        self, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_five_percent_paper_trade(self, trade_id: int) -> dict[str, Any] | None: ...

    def update_five_percent_paper_trade(
        self, trade_id: int, update: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def save_bookmark(self, user_id: int, symbol: str, price: float) -> dict[str, Any]: ...

    def list_bookmarks(self, user_id: int) -> list[dict[str, Any]]: ...

    def delete_bookmark(self, user_id: int, symbol: str) -> bool: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


class NullSnapshotRepository:
    """Persistence-disabled implementation used when DATABASE_URL is absent."""

    def save_signal(self, snapshot: dict[str, Any]) -> None:
        return None

    def save_market_scan(
        self, snapshot: dict[str, Any], idempotency_key: str | None = None
    ) -> None:
        del idempotency_key
        return None

    def save_market_quotes(self, snapshot: dict[str, Any]) -> int:
        self._market_quotes = snapshot
        return len(snapshot.get("prices", {}))

    def save_alerts(self, snapshot: dict[str, Any], idempotency_key: str) -> int:
        del snapshot, idempotency_key
        return 0

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        del limit
        return []

    def signal_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def save_weekly_predictions(self, snapshot: dict[str, Any]) -> int:
        del snapshot
        return 0

    def latest_weekly_predictions(
        self, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]:
        del max_price, limit_per_sector
        return {
            "generated_at": None,
            "market_date": None,
            "valid_until": None,
            "model": None,
            "predictions_count": 0,
            "sectors": [],
        }

    def weekly_prediction_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        del symbol, limit
        return []

    def save_monthly_predictions(self, snapshot: dict[str, Any]) -> int:
        del snapshot
        return 0

    def latest_monthly_predictions(
        self, horizon_months: int, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]:
        del max_price, limit_per_sector
        return {
            "generated_at": None,
            "market_date": None,
            "horizon_months": horizon_months,
            "model": None,
            "predictions_count": 0,
            "score_method": {},
            "sectors": [],
        }

    def monthly_prediction_history(
        self, symbol: str, horizon_months: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        del symbol, horizon_months, limit
        return []

    def latest_alpha_features(
        self, symbols: list[str]
    ) -> dict[str, dict[str, Any]]:
        del symbols
        return {}

    def save_alpha_ranking(self, snapshot: dict[str, Any]) -> int:
        self._alpha_ranking = snapshot
        return int(snapshot.get("predictions_count", 0))

    def latest_alpha_ranking(
        self, horizon: str, horizon_months: int = 1
    ) -> dict[str, Any] | None:
        value = getattr(self, "_alpha_ranking", None)
        if value and value.get("horizon") == horizon:
            if horizon != "monthly" or value.get("horizon_months") == horizon_months:
                return value
        return None

    def save_ml_predictions(self, snapshot: dict[str, Any]) -> int:
        self._ml_predictions = snapshot
        return int(snapshot.get("predictions_count", 0))

    def latest_ml_predictions(self, limit: int = 20) -> dict[str, Any] | None:
        value = getattr(self, "_ml_predictions", None)
        if value is None:
            return None
        return {**value, "predictions": value.get("predictions", [])[:limit]}

    def save_growth_features(self, snapshot: dict[str, Any]) -> int:
        values = getattr(self, "_growth_features", {})
        values[snapshot["symbol"]] = snapshot
        self._growth_features = values
        return 1

    def save_filing_document(self, snapshot: dict[str, Any]) -> int:
        documents = getattr(self, "_filing_documents", [])
        documents.append(snapshot)
        self._filing_documents = documents
        return 1

    def latest_growth_features(
        self, symbols: list[str], known_at: str | None = None
    ) -> dict[str, dict[str, Any]]:
        del known_at
        values = getattr(self, "_growth_features", {})
        return {symbol: values[symbol] for symbol in symbols if symbol in values}

    def save_growth_radar(self, snapshot: dict[str, Any]) -> int:
        self._growth_radar = snapshot
        signals = getattr(self, "_growth_first_signals", {})
        for item in snapshot.get("candidates", []):
            if item.get("state") not in {"QUALIFIED", "BREAKOUT_CONFIRMED"}:
                continue
            signals.setdefault(
                item["symbol"],
                {
                    "signal_date": item["as_of"],
                    "signal_price": item["current_price"],
                    "initial_state": item["state"],
                    "initial_score": item["strength_score"],
                },
            )
        self._growth_first_signals = signals
        return len(snapshot.get("candidates", []))

    def latest_growth_radar(self) -> dict[str, Any] | None:
        return getattr(self, "_growth_radar", None)

    def first_growth_signal(self, symbol: str) -> dict[str, Any] | None:
        return getattr(self, "_growth_first_signals", {}).get(symbol)

    def save_paper_portfolio(self, snapshot: dict[str, Any]) -> None:
        self._paper_portfolio = snapshot

    def latest_paper_portfolio(self) -> dict[str, Any] | None:
        return getattr(self, "_paper_portfolio", None)

    def save_five_percent_strategy_run(self, snapshot: dict[str, Any]) -> int:
        self._five_percent_run = snapshot
        history = getattr(self, "_five_percent_runs_by_id", {})
        history[snapshot["run_id"]] = snapshot
        self._five_percent_runs_by_id = history
        symbol_history = getattr(self, "_five_percent_symbol_history", {})
        for candidate in snapshot.get("candidates", []):
            symbol_history.setdefault(candidate["symbol"], []).insert(0, candidate)
        self._five_percent_symbol_history = symbol_history
        return len(snapshot.get("candidates", []))

    def latest_five_percent_strategy_run(self) -> dict[str, Any]:
        value = getattr(self, "_five_percent_run", None)
        if value is not None:
            return value
        return {
            "run_id": None,
            "created_at": None,
            "market_date": None,
            "candidates_count": 0,
            "candidates": [],
        }

    def five_percent_strategy_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return getattr(self, "_five_percent_runs_by_id", {}).get(run_id)

    def five_percent_strategy_symbol_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        values = getattr(self, "_five_percent_symbol_history", {}).get(symbol, [])
        return values[:limit]

    def save_five_percent_backtest_run(self, snapshot: dict[str, Any]) -> int:
        backtests = getattr(self, "_five_percent_backtests", {})
        backtests[snapshot["backtest_id"]] = snapshot
        self._five_percent_backtests = backtests
        return int(snapshot.get("total_trades", 0))

    def save_five_percent_paper_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        trades = getattr(self, "_five_percent_paper_trades", {})
        next_id = getattr(self, "_five_percent_paper_trade_seq", 0) + 1
        self._five_percent_paper_trade_seq = next_id
        record = {"id": next_id, **trade}
        trades[next_id] = record
        self._five_percent_paper_trades = trades
        return record

    def list_five_percent_paper_trades(
        self, status: str | None = None
    ) -> list[dict[str, Any]]:
        trades = getattr(self, "_five_percent_paper_trades", {}).values()
        if status is not None:
            trades = [trade for trade in trades if trade.get("status") == status]
        return sorted(trades, key=lambda item: item["id"], reverse=True)

    def get_five_percent_paper_trade(self, trade_id: int) -> dict[str, Any] | None:
        return getattr(self, "_five_percent_paper_trades", {}).get(trade_id)

    def update_five_percent_paper_trade(
        self, trade_id: int, update: dict[str, Any]
    ) -> dict[str, Any] | None:
        trades = getattr(self, "_five_percent_paper_trades", {})
        record = trades.get(trade_id)
        if record is None:
            return None
        record.update(update)
        return record

    def save_bookmark(self, user_id: int, symbol: str, price: float) -> dict[str, Any]:
        from datetime import datetime, timezone

        bookmarks = getattr(self, "_bookmarks", {})
        key = (user_id, symbol)
        next_id = getattr(self, "_bookmark_seq", 0) + 1
        self._bookmark_seq = next_id
        record = {
            "id": bookmarks.get(key, {}).get("id", next_id),
            "user_id": user_id,
            "symbol": symbol,
            "bookmark_price": price,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        bookmarks[key] = record
        self._bookmarks = bookmarks
        return record

    def list_bookmarks(self, user_id: int) -> list[dict[str, Any]]:
        matches = [
            record
            for (uid, _symbol), record in getattr(self, "_bookmarks", {}).items()
            if uid == user_id
        ]
        return sorted(matches, key=lambda item: item["created_at"], reverse=True)

    def delete_bookmark(self, user_id: int, symbol: str) -> bool:
        bookmarks = getattr(self, "_bookmarks", {})
        return bookmarks.pop((user_id, symbol), None) is not None

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None
