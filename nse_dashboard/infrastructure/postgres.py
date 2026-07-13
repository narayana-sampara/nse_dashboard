from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nse_dashboard.core.json import json_ready


def _ranked_prediction_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten signal lists first, then retain legacy picks without duplicates."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sector in snapshot.get("sectors", []):
        for list_name, rank_name in (("buys", "buy_rank"), ("sells", "sell_rank"), ("picks", "sector_rank")):
            for original in sector.get(list_name, []):
                symbol = str(original["symbol"])
                if symbol in seen:
                    continue
                item = dict(original)
                item["sector_rank"] = int(item.get(rank_name, item.get("sector_rank", 1)))
                rows.append(json_ready(item))
                seen.add(symbol)
    return rows


class PostgresSnapshotRepository:
    """Historical signal and dashboard snapshots stored in PostgreSQL/TimescaleDB."""

    def __init__(self, url: str, connect_timeout: float = 2.0) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError("Install 'psycopg' to use DATABASE_URL") from exc
        self._psycopg = psycopg
        self._url = url
        self._connect_timeout = max(1, int(connect_timeout))

    def _connect(self):
        return self._psycopg.connect(self._url, connect_timeout=self._connect_timeout)

    def save_signal(self, snapshot: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO signal_snapshots
                    (captured_at, symbol, strategy, signal, price, source, market_time, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    datetime.now(timezone.utc),
                    snapshot["symbol"],
                    snapshot.get("strategy", "composite_technical"),
                    snapshot["signal"],
                    snapshot.get("price"),
                    snapshot.get("source"),
                    snapshot.get("as_of"),
                    Jsonb(json_ready(snapshot)),
                ),
            )

    def save_market_scan(
        self, snapshot: dict[str, Any], idempotency_key: str | None = None
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            if idempotency_key and not self._claim_task_key(cursor, f"snapshot:{idempotency_key}"):
                return
            cursor.execute(
                """
                INSERT INTO market_scan_snapshots
                    (captured_at, market_date, strategy, source, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    snapshot["generated_at"],
                    snapshot.get("market_date"),
                    snapshot["strategy"],
                    snapshot.get("source"),
                    Jsonb(json_ready(snapshot)),
                ),
            )

    def save_market_quotes(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        prices = snapshot.get("prices", {})
        with self._connect() as connection, connection.cursor() as cursor:
            created = 0
            for symbol, quote in prices.items():
                cursor.execute(
                    """
                    INSERT INTO market_quote_snapshots
                        (captured_at, symbol, source, price, market_time, payload)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot["fetched_at"],
                        symbol,
                        snapshot.get("source"),
                        quote.get("price"),
                        quote.get("market_time"),
                        Jsonb(json_ready(quote)),
                    ),
                )
                created += cursor.rowcount
        return created

    @staticmethod
    def _claim_task_key(cursor, key: str) -> bool:
        cursor.execute(
            "INSERT INTO worker_task_keys (key) VALUES (%s) "
            "ON CONFLICT (key) DO NOTHING RETURNING key",
            (key,),
        )
        return cursor.fetchone() is not None

    def save_alerts(self, snapshot: dict[str, Any], idempotency_key: str) -> int:
        from psycopg.types.json import Jsonb

        candidates = [
            item
            for sector in snapshot.get("sectors", [])
            for side in ("buys", "sells")
            for item in sector.get(side, [])
        ]
        with self._connect() as connection, connection.cursor() as cursor:
            if not self._claim_task_key(cursor, f"alerts:{idempotency_key}"):
                return 0
            created = 0
            for item in candidates:
                cursor.execute(
                    "SELECT signal FROM signal_alerts WHERE symbol = %s ORDER BY created_at DESC LIMIT 1",
                    (item["symbol"],),
                )
                previous = cursor.fetchone()
                if previous is not None and previous[0] == item["signal"]:
                    continue
                cursor.execute(
                    """
                    INSERT INTO signal_alerts
                        (created_at, run_key, symbol, sector, signal, score, price, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_key, symbol, signal) DO NOTHING
                    """,
                    (
                        snapshot["generated_at"],
                        idempotency_key,
                        item["symbol"],
                        item.get("sector"),
                        item["signal"],
                        item["score"],
                        item.get("price"),
                        Jsonb(json_ready(item)),
                    ),
                )
                created += cursor.rowcount
        return created

    def recent_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT created_at, run_key, symbol, sector, signal, score, price, payload
                FROM signal_alerts
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def signal_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT captured_at, symbol, strategy, signal, price, source, market_time, payload
                FROM signal_snapshots
                WHERE symbol = %s
                ORDER BY captured_at DESC
                LIMIT %s
                """,
                (symbol, limit),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_weekly_predictions(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        predictions = _ranked_prediction_rows(snapshot)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO weekly_prediction_runs
                    (generated_at, market_date, valid_until, model_name, model_version,
                     filters, universe_size, eligible_stocks, failures, monthly_regime)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (market_date, model_version)
                DO UPDATE SET
                    generated_at = EXCLUDED.generated_at,
                    valid_until = EXCLUDED.valid_until,
                    filters = EXCLUDED.filters,
                    universe_size = EXCLUDED.universe_size,
                    eligible_stocks = EXCLUDED.eligible_stocks,
                    failures = EXCLUDED.failures,
                    monthly_regime = EXCLUDED.monthly_regime
                RETURNING id
                """,
                (
                    snapshot["generated_at"], snapshot["market_date"],
                    snapshot["valid_until"], snapshot["model"]["name"],
                    snapshot["model"]["version"], Jsonb(json_ready(snapshot["filters"])),
                    snapshot["universe_size"], snapshot["eligible_stocks"],
                    Jsonb(json_ready(snapshot["failures"])), snapshot.get("monthly_regime", "UNAVAILABLE"),
                ),
            )
            run_id = cursor.fetchone()[0]
            cursor.execute("DELETE FROM weekly_predictions WHERE run_id = %s", (run_id,))
            created = 0
            for item in predictions:
                cursor.execute(
                    """
                    INSERT INTO weekly_predictions
                        (run_id, generated_at, market_date, valid_until, symbol, sector,
                         sector_rank, price, predicted_return_pct, target_probability,
                         ranking_score, risk_score, model_name, model_version, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_date, model_version, symbol)
                    DO UPDATE SET
                        generated_at = EXCLUDED.generated_at,
                        valid_until = EXCLUDED.valid_until,
                        sector_rank = EXCLUDED.sector_rank,
                        price = EXCLUDED.price,
                        predicted_return_pct = EXCLUDED.predicted_return_pct,
                        target_probability = EXCLUDED.target_probability,
                        ranking_score = EXCLUDED.ranking_score,
                        risk_score = EXCLUDED.risk_score,
                        payload = EXCLUDED.payload
                    """,
                    (
                        run_id,
                        snapshot["generated_at"],
                        snapshot["market_date"],
                        snapshot["valid_until"],
                        item["symbol"],
                        item["sector"],
                        item["sector_rank"],
                        item["price"],
                        item["predicted_5d_return_pct"],
                        item["target_probability"],
                        item["ranking_score"],
                        item["risk_score"],
                        snapshot["model"]["name"],
                        snapshot["model"]["version"],
                        Jsonb(json_ready(item)),
                    ),
                )
                created += cursor.rowcount
        return created

    def latest_weekly_predictions(
        self, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, generated_at, market_date, valid_until, model_name,
                       model_version, universe_size, eligible_stocks, filters, monthly_regime
                FROM weekly_prediction_runs
                ORDER BY market_date DESC, generated_at DESC
                LIMIT 1
                """
            )
            run = cursor.fetchone()
            if run is None:
                return {
                    "generated_at": None,
                    "market_date": None,
                    "valid_until": None,
                    "model": None,
                    "predictions_count": 0,
                    "sectors": [],
                }
            query = """
                SELECT generated_at, market_date, valid_until, sector, model_name,
                       model_version, payload
                FROM weekly_predictions
                WHERE run_id = %s
            """
            parameters: list[Any] = [run["id"]]
            if max_price is not None:
                query += " AND price <= %s"
                parameters.append(max_price)
            query += """
                AND sector_rank <= %s
                ORDER BY sector, sector_rank
            """
            parameters.append(limit_per_sector)
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        grouped_buys: dict[str, list[dict[str, Any]]] = {}
        grouped_sells: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = row["payload"]
            sector = row["sector"]
            grouped.setdefault(sector, []).append(payload)
            signal = payload.get("indicator", {}).get("signal")
            if signal == "BUY":
                grouped_buys.setdefault(sector, []).append(payload)
            elif signal == "SELL":
                grouped_sells.setdefault(sector, []).append(payload)
        return {
            "generated_at": run["generated_at"],
            "market_date": run["market_date"],
            "valid_until": run["valid_until"],
            "model": {"name": run["model_name"], "version": run["model_version"]},
            "universe_size": run["universe_size"],
            "eligible_stocks": run["eligible_stocks"],
            "filters": run["filters"],
            "monthly_regime": run["monthly_regime"],
            "predictions_count": len(rows),
            "buy_count": sum(map(len, grouped_buys.values())),
            "sell_count": sum(map(len, grouped_sells.values())),
            "sectors": [
                {"name": sector, "picks": picks, "buys": grouped_buys.get(sector, []),
                 "sells": grouped_sells.get(sector, [])}
                for sector, picks in grouped.items()
            ],
        }

    def weekly_prediction_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT generated_at, market_date, valid_until, model_name,
                       model_version, payload
                FROM weekly_predictions
                WHERE symbol = %s
                ORDER BY market_date DESC
                LIMIT %s
                """,
                (symbol, limit),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_monthly_predictions(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        predictions = _ranked_prediction_rows(snapshot)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO monthly_prediction_runs
                    (generated_at, market_date, horizon_months, model_name,
                     model_version, filters, score_method, universe_size,
                     eligible_stocks, failures, regime, strategy_name,
                     strategy_version, selection_method)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (market_date, horizon_months, model_version)
                DO UPDATE SET
                    generated_at = EXCLUDED.generated_at,
                    filters = EXCLUDED.filters,
                    score_method = EXCLUDED.score_method,
                    universe_size = EXCLUDED.universe_size,
                    eligible_stocks = EXCLUDED.eligible_stocks,
                    failures = EXCLUDED.failures,
                    regime = EXCLUDED.regime,
                    strategy_name = EXCLUDED.strategy_name,
                    strategy_version = EXCLUDED.strategy_version,
                    selection_method = EXCLUDED.selection_method
                RETURNING id
                """,
                (
                    snapshot["generated_at"], snapshot["market_date"],
                    snapshot["horizon_months"], snapshot["model"]["name"],
                    snapshot["model"]["version"], Jsonb(json_ready(snapshot["filters"])),
                    Jsonb(json_ready(snapshot["score_method"])), snapshot["universe_size"],
                    snapshot["eligible_stocks"], Jsonb(json_ready(snapshot["failures"])),
                    Jsonb(json_ready(snapshot.get("regime", {}))),
                    snapshot.get("strategy", {}).get("name", "conservative_nse_monthly"),
                    snapshot.get("strategy", {}).get("version", "2.0.0"),
                    Jsonb(json_ready(snapshot.get("selection_method", {}))),
                ),
            )
            run_id = cursor.fetchone()[0]
            cursor.execute("DELETE FROM monthly_predictions WHERE run_id = %s", (run_id,))
            created = 0
            for item in predictions:
                cursor.execute(
                    """
                    INSERT INTO monthly_predictions
                        (run_id, generated_at, market_date, horizon_months, symbol,
                         sector, sector_rank, price, predicted_return_pct,
                         target_probability, score, risk_score, model_name,
                         model_version, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (market_date, horizon_months, model_version, symbol)
                    DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        generated_at = EXCLUDED.generated_at,
                        sector_rank = EXCLUDED.sector_rank,
                        price = EXCLUDED.price,
                        predicted_return_pct = EXCLUDED.predicted_return_pct,
                        target_probability = EXCLUDED.target_probability,
                        score = EXCLUDED.score,
                        risk_score = EXCLUDED.risk_score,
                        payload = EXCLUDED.payload
                    """,
                    (
                        run_id, snapshot["generated_at"], snapshot["market_date"],
                        snapshot["horizon_months"], item["symbol"], item["sector"],
                        item["sector_rank"], item["price"],
                        item["predicted_return_pct"], item["target_probability"],
                        item["score"], item["risk_score"],
                        snapshot["model"]["name"], snapshot["model"]["version"],
                        Jsonb(json_ready(item)),
                    ),
                )
                created += cursor.rowcount
        return created

    def latest_monthly_predictions(
        self, horizon_months: int, max_price: float | None = None, limit_per_sector: int = 5
    ) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, generated_at, market_date, horizon_months, model_name,
                       model_version, filters, score_method, universe_size,
                       eligible_stocks, regime, strategy_name, strategy_version,
                       selection_method
                FROM monthly_prediction_runs
                WHERE horizon_months = %s
                ORDER BY market_date DESC, generated_at DESC
                LIMIT 1
                """,
                (horizon_months,),
            )
            run = cursor.fetchone()
            if run is None:
                return {
                    "generated_at": None, "market_date": None,
                    "horizon_months": horizon_months, "model": None,
                    "predictions_count": 0, "score_method": {}, "sectors": [],
                }
            query = """
                SELECT sector, payload
                FROM monthly_predictions
                WHERE run_id = %s
            """
            parameters: list[Any] = [run["id"]]
            if max_price is not None:
                query += " AND price <= %s"
                parameters.append(max_price)
            query += """
                AND sector_rank <= %s
                ORDER BY sector, sector_rank
            """
            parameters.append(limit_per_sector)
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        grouped_buys: dict[str, list[dict[str, Any]]] = {}
        grouped_sells: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = row["payload"]
            sector = row["sector"]
            grouped.setdefault(sector, []).append(payload)
            signal = payload.get("indicator", {}).get("signal")
            if signal == "BUY":
                grouped_buys.setdefault(sector, []).append(payload)
            elif signal == "SELL":
                grouped_sells.setdefault(sector, []).append(payload)
        return {
            "generated_at": run["generated_at"],
            "market_date": run["market_date"],
            "horizon_months": run["horizon_months"],
            "model": {"name": run["model_name"], "version": run["model_version"]},
            "filters": run["filters"],
            "score_method": run["score_method"],
            "regime": run["regime"],
            "strategy": {"name": run["strategy_name"], "version": run["strategy_version"]},
            "selection_method": run["selection_method"],
            "universe_size": run["universe_size"],
            "eligible_stocks": run["eligible_stocks"],
            "predictions_count": len(rows),
            "buy_count": sum(map(len, grouped_buys.values())),
            "sell_count": sum(map(len, grouped_sells.values())),
            "sectors": [
                {"name": sector, "picks": picks, "buys": grouped_buys.get(sector, []),
                 "sells": grouped_sells.get(sector, [])}
                for sector, picks in grouped.items()
            ],
        }

    def monthly_prediction_history(
        self, symbol: str, horizon_months: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        query = """
            SELECT generated_at, market_date, horizon_months, model_name,
                   model_version, payload
            FROM monthly_predictions
            WHERE symbol = %s
        """
        parameters: list[Any] = [symbol]
        if horizon_months is not None:
            query += " AND horizon_months = %s"
            parameters.append(horizon_months)
        query += " ORDER BY market_date DESC LIMIT %s"
        parameters.append(limit)
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_fundamental_features(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fundamental_feature_snapshots
                    (symbol, fiscal_period_end, period_type, source, source_version,
                     published_at, known_at, payload_hash, score, grade, coverage,
                     features, contributions, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT
                    (symbol, fiscal_period_end, period_type, source, source_version, payload_hash)
                DO NOTHING
                """,
                (
                    snapshot["symbol"],
                    snapshot["fiscal_period_end"],
                    snapshot.get("period_type", "TTM"),
                    snapshot["source"],
                    snapshot.get("source_version", "1"),
                    snapshot["published_at"],
                    snapshot.get("known_at", snapshot["published_at"]),
                    snapshot["payload_hash"],
                    snapshot["score"],
                    snapshot["grade"],
                    snapshot.get("coverage", "FULL"),
                    Jsonb(json_ready(snapshot.get("features", {}))),
                    Jsonb(json_ready(snapshot.get("contributions", {}))),
                    Jsonb(json_ready(snapshot.get("raw_payload", {}))),
                ),
            )
            return cursor.rowcount

    def save_sentiment_features(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sentiment_feature_snapshots
                    (symbol, as_of, model_name, model_version, score,
                     composite_score, trend, coverage, features, contributions)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, as_of, model_version)
                DO UPDATE SET score = EXCLUDED.score,
                    composite_score = EXCLUDED.composite_score,
                    trend = EXCLUDED.trend,
                    coverage = EXCLUDED.coverage,
                    features = EXCLUDED.features,
                    contributions = EXCLUDED.contributions
                """,
                (
                    snapshot["symbol"],
                    snapshot["as_of"],
                    snapshot.get("model_name", "finbert"),
                    snapshot.get("model_version", "baseline"),
                    snapshot["score"],
                    snapshot["composite_score"],
                    snapshot["trend"],
                    snapshot.get("coverage", "FULL"),
                    Jsonb(json_ready(snapshot.get("features", {}))),
                    Jsonb(json_ready(snapshot.get("contributions", {}))),
                ),
            )
            return cursor.rowcount

    def save_legal_risk(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO legal_risk_snapshots
                    (symbol, as_of, source_version, risk_quotient, risk_flag,
                     coverage, features, contributions)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, as_of, source_version)
                DO UPDATE SET risk_quotient = EXCLUDED.risk_quotient,
                    risk_flag = EXCLUDED.risk_flag,
                    coverage = EXCLUDED.coverage,
                    features = EXCLUDED.features,
                    contributions = EXCLUDED.contributions
                """,
                (
                    snapshot["symbol"],
                    snapshot["as_of"],
                    snapshot.get("source_version", "legal-v1"),
                    snapshot["risk_quotient"],
                    snapshot["risk_flag"],
                    snapshot.get("coverage", "FULL"),
                    Jsonb(json_ready(snapshot.get("features", {}))),
                    Jsonb(json_ready(snapshot.get("contributions", {}))),
                ),
            )
            return cursor.rowcount

    def save_options_features(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO options_feature_snapshots
                    (symbol, as_of, model_version, score, coverage, features, contributions)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, as_of, model_version)
                DO UPDATE SET score = EXCLUDED.score,
                    coverage = EXCLUDED.coverage,
                    features = EXCLUDED.features,
                    contributions = EXCLUDED.contributions
                """,
                (
                    snapshot["symbol"],
                    snapshot["as_of"],
                    snapshot.get("model_version", "smart-money-v1"),
                    snapshot["score"],
                    snapshot.get("coverage", "FULL"),
                    Jsonb(json_ready(snapshot.get("features", {}))),
                    Jsonb(json_ready(snapshot.get("contributions", {}))),
                ),
            )
            return cursor.rowcount

    def latest_alpha_features(
        self, symbols: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        from psycopg.rows import dict_row

        result: dict[str, dict[str, Any]] = {symbol: {} for symbol in symbols}
        queries = {
            "fundamental": """
                SELECT DISTINCT ON (symbol) symbol, score, grade, coverage,
                       known_at AS as_of, features, contributions
                FROM fundamental_feature_snapshots
                WHERE symbol = ANY(%s)
                ORDER BY symbol, known_at DESC, fiscal_period_end DESC
            """,
            "sentiment": """
                SELECT DISTINCT ON (symbol) symbol, score, composite_score, trend,
                       coverage, as_of, features, contributions
                FROM sentiment_feature_snapshots
                WHERE symbol = ANY(%s)
                ORDER BY symbol, as_of DESC
            """,
            "legal": """
                SELECT DISTINCT ON (symbol) symbol, risk_quotient, risk_flag,
                       coverage, as_of, features, contributions
                FROM legal_risk_snapshots
                WHERE symbol = ANY(%s)
                ORDER BY symbol, as_of DESC
            """,
            "options": """
                SELECT DISTINCT ON (symbol) symbol, score, coverage, as_of,
                       features, contributions
                FROM options_feature_snapshots
                WHERE symbol = ANY(%s)
                ORDER BY symbol, as_of DESC
            """,
        }
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            for factor, query in queries.items():
                cursor.execute(query, (symbols,))
                for row in cursor.fetchall():
                    item = dict(row)
                    symbol = item.pop("symbol")
                    result.setdefault(symbol, {})[factor] = json_ready(item)
        return result

    def save_alpha_ranking(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alpha_ranking_runs
                    (generated_at, market_date, horizon, horizon_months, model_name,
                     model_version, feature_set_version, weights, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot["generated_at"],
                    snapshot.get("market_date"),
                    snapshot["horizon"],
                    snapshot.get("horizon_months"),
                    snapshot["model"]["name"],
                    snapshot["model"]["version"],
                    snapshot["model"]["feature_set_version"],
                    Jsonb(json_ready(snapshot["base_weights"])),
                    Jsonb(json_ready(snapshot)),
                ),
            )
            cursor.fetchone()
        return int(snapshot.get("predictions_count", 0))

    def latest_alpha_ranking(
        self, horizon: str, horizon_months: int = 1
    ) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        query = """
            SELECT payload
            FROM alpha_ranking_runs
            WHERE horizon = %s
        """
        parameters: list[Any] = [horizon]
        if horizon == "monthly":
            query += " AND horizon_months = %s"
            parameters.append(horizon_months)
        query += " ORDER BY market_date DESC NULLS LAST, generated_at DESC LIMIT 1"
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters)
            row = cursor.fetchone()
        return row["payload"] if row else None

    def save_ml_predictions(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ml_prediction_runs
                    (generated_at, model_name, model_version, feature_set_version,
                     universe_size, predictions_count, failures, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot["generated_at"],
                    snapshot.get("model", {}).get("name", "lightgbm_forward_1y"),
                    snapshot.get("model_version", snapshot.get("model", {}).get("version")),
                    snapshot.get("model", {}).get("feature_set_version", "ml-features-v1"),
                    snapshot.get("universe_size", 0),
                    snapshot.get("predictions_count", 0),
                    Jsonb(json_ready(snapshot.get("failures", []))),
                    Jsonb(json_ready(snapshot)),
                ),
            )
            run_id = cursor.fetchone()[0]
            created = 0
            for item in snapshot.get("predictions", []):
                cursor.execute(
                    """
                    INSERT INTO ml_predictions
                        (run_id, generated_at, model_version, symbol, name, sector,
                         rank, current_price, target_price_1y, implied_cagr_pct,
                         probability_positive, conviction, shap_values, dynamic_thesis, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, symbol) DO UPDATE SET
                        rank = EXCLUDED.rank,
                        current_price = EXCLUDED.current_price,
                        target_price_1y = EXCLUDED.target_price_1y,
                        implied_cagr_pct = EXCLUDED.implied_cagr_pct,
                        probability_positive = EXCLUDED.probability_positive,
                        conviction = EXCLUDED.conviction,
                        shap_values = EXCLUDED.shap_values,
                        dynamic_thesis = EXCLUDED.dynamic_thesis,
                        payload = EXCLUDED.payload
                    """,
                    (
                        run_id,
                        snapshot["generated_at"],
                        snapshot.get("model_version", snapshot.get("model", {}).get("version")),
                        item["symbol"],
                        item.get("name"),
                        item.get("sector"),
                        item.get("rank"),
                        item.get("current_price"),
                        item.get("target_price_1y"),
                        item.get("implied_cagr_pct"),
                        item.get("probability_positive"),
                        item.get("conviction"),
                        Jsonb(json_ready(item.get("shap_values", {}))),
                        item.get("dynamic_thesis"),
                        Jsonb(json_ready(item)),
                    ),
                )
                created += cursor.rowcount
        return created

    def latest_ml_predictions(self, limit: int = 20) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, generated_at, model_name, model_version,
                       feature_set_version, universe_size, predictions_count,
                       failures, payload
                FROM ml_prediction_runs
                ORDER BY generated_at DESC
                LIMIT 1
                """
            )
            run = cursor.fetchone()
            if run is None:
                return None
            cursor.execute(
                """
                SELECT payload
                FROM ml_predictions
                WHERE run_id = %s
                ORDER BY rank
                LIMIT %s
                """,
                (run["id"], limit),
            )
            rows = cursor.fetchall()
        payload = dict(run["payload"])
        payload["predictions"] = [row["payload"] for row in rows]
        payload["predictions_count"] = run["predictions_count"]
        return json_ready(payload)

    def save_growth_features(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO growth_factor_snapshots
                    (symbol, as_of, known_at, source_version, freshness_status,
                     features, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, as_of, known_at, source_version)
                DO UPDATE SET freshness_status = EXCLUDED.freshness_status,
                    features = EXCLUDED.features,
                    evidence = EXCLUDED.evidence
                """,
                (
                    snapshot["symbol"],
                    snapshot["as_of"],
                    snapshot["known_at"],
                    snapshot.get("source_version", "growth-features-v1"),
                    snapshot.get("freshness_status", "CURRENT"),
                    Jsonb(json_ready(snapshot.get("features", {}))),
                    Jsonb(json_ready(snapshot.get("evidence", []))),
                ),
            )
            return cursor.rowcount

    def save_filing_document(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO filing_documents
                    (symbol, document_type, source, source_url, published_at,
                     known_at, payload_hash, extraction_status, raw_payload,
                     extracted_features)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, source, source_url, payload_hash)
                DO UPDATE SET extraction_status = EXCLUDED.extraction_status,
                    extracted_features = EXCLUDED.extracted_features
                """,
                (
                    snapshot["symbol"],
                    snapshot["document_type"],
                    snapshot["source"],
                    snapshot["source_url"],
                    snapshot["published_at"],
                    snapshot.get("known_at", snapshot["published_at"]),
                    snapshot["payload_hash"],
                    snapshot.get("extraction_status", "COMPLETE"),
                    Jsonb(json_ready(snapshot.get("raw_payload", {}))),
                    Jsonb(json_ready(snapshot.get("extracted_features", {}))),
                ),
            )
            return cursor.rowcount

    def latest_growth_features(
        self, symbols: list[str], known_at: str | None = None
    ) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        from psycopg.rows import dict_row

        query = """
            SELECT DISTINCT ON (symbol) symbol, as_of, known_at,
                   source_version, freshness_status, features, evidence
            FROM growth_factor_snapshots
            WHERE symbol = ANY(%s)
        """
        parameters: list[Any] = [symbols]
        if known_at is not None:
            query += " AND known_at <= %s"
            parameters.append(known_at)
        query += " ORDER BY symbol, known_at DESC, as_of DESC"
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        return {
            row["symbol"]: json_ready(
                {
                    "as_of": row["as_of"],
                    "known_at": row["known_at"],
                    "source_version": row["source_version"],
                    "freshness_status": row["freshness_status"],
                    "features": row["features"],
                    "evidence": row["evidence"],
                }
            )
            for row in rows
        }

    def save_growth_radar(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO growth_radar_runs
                    (generated_at, market_date, model_name, model_version,
                     feature_set_version, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot["generated_at"],
                    snapshot.get("market_date"),
                    snapshot["model"]["name"],
                    snapshot["model"]["version"],
                    snapshot["model"]["feature_set_version"],
                    Jsonb(json_ready(snapshot)),
                ),
            )
            run_id = cursor.fetchone()[0]
            for item in snapshot.get("candidates", []):
                if item.get("state") in {"QUALIFIED", "BREAKOUT_CONFIRMED"}:
                    cursor.execute(
                        """
                        INSERT INTO growth_first_signals
                            (symbol, signal_date, signal_price, initial_state,
                             initial_score, radar_run_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol) DO NOTHING
                        """,
                        (
                            item["symbol"],
                            item["as_of"],
                            item["current_price"],
                            item["state"],
                            item["strength_score"],
                            run_id,
                        ),
                    )
                projection = item.get("projections", {})
                if projection.get("available"):
                    cursor.execute(
                        """
                        INSERT INTO growth_projection_runs
                            (radar_run_id, symbol, generated_at, current_price,
                             confidence_pct, payload)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_id,
                            item["symbol"],
                            snapshot["generated_at"],
                            item["current_price"],
                            item["confidence_pct"],
                            Jsonb(json_ready(projection)),
                        ),
                    )
        return len(snapshot.get("candidates", []))

    def latest_growth_radar(self) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM growth_radar_runs
                ORDER BY market_date DESC NULLS LAST, generated_at DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        return row["payload"] if row else None

    def first_growth_signal(self, symbol: str) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT signal_date, signal_price, initial_state, initial_score
                FROM growth_first_signals
                WHERE symbol = %s
                """,
                (symbol,),
            )
            row = cursor.fetchone()
        return json_ready(dict(row)) if row else None

    def ping(self) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)

    def save_paper_portfolio(self, snapshot: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO paper_portfolio_snapshots (strategy_version, payload) VALUES (%s, %s)",
                (snapshot.get("strategy_version", "2.0.0"), Jsonb(json_ready(snapshot))),
            )

    def latest_paper_portfolio(self) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT payload FROM paper_portfolio_snapshots ORDER BY captured_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
        return row["payload"] if row else None

    def save_five_percent_strategy_run(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        candidates = snapshot.get("candidates", [])
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO five_percent_strategy_runs
                    (run_id, created_at, market_date, strategy_version, model_version,
                     target_pct, stop_loss_pct, holding_days, probability_threshold,
                     initial_capital, max_candidates, status, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot["run_id"],
                    snapshot["created_at"],
                    snapshot["market_date"],
                    snapshot["strategy_version"],
                    snapshot["model_version"],
                    snapshot["target_pct"],
                    snapshot["stop_loss_pct"],
                    snapshot["holding_days"],
                    snapshot["probability_threshold"],
                    snapshot["initial_capital"],
                    snapshot["max_candidates"],
                    snapshot["status"],
                    Jsonb(json_ready({"skipped": snapshot.get("skipped", [])})),
                ),
            )
            run_id = cursor.fetchone()[0]
            for candidate in candidates:
                cursor.execute(
                    """
                    INSERT INTO five_percent_strategy_candidates
                        (run_id, symbol, company_name, sector, close_price, entry_price,
                         target_price, stop_loss_price, probability_score, ai_score, rank,
                         expected_return_pct, risk_reward_ratio, avg_volume, avg_turnover,
                         volatility, rsi, momentum_5d, momentum_20d, volume_ratio, trend_score,
                         relative_strength_score, breakout_score, risk_score, reasons, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        candidate["symbol"],
                        candidate.get("company_name"),
                        candidate.get("sector"),
                        candidate["close_price"],
                        candidate["entry_price"],
                        candidate["target_price"],
                        candidate["stop_loss_price"],
                        candidate["probability_score"],
                        candidate["ai_score"],
                        candidate["rank"],
                        candidate["expected_return_pct"],
                        candidate["risk_reward_ratio"],
                        candidate.get("avg_volume"),
                        candidate.get("avg_turnover"),
                        candidate.get("volatility"),
                        candidate.get("rsi"),
                        candidate.get("momentum_5d"),
                        candidate.get("momentum_20d"),
                        candidate.get("volume_ratio"),
                        candidate.get("trend_score"),
                        candidate.get("relative_strength_score"),
                        candidate.get("breakout_score"),
                        candidate.get("risk_score"),
                        Jsonb(json_ready(candidate.get("reasons", []))),
                        snapshot["created_at"],
                    ),
                )
        return len(candidates)

    def latest_five_percent_strategy_run(self) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, run_id, created_at, market_date, strategy_version, model_version,
                       target_pct, stop_loss_pct, holding_days, probability_threshold,
                       initial_capital, max_candidates, status, metadata
                FROM five_percent_strategy_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            run = cursor.fetchone()
            if run is None:
                return {
                    "run_id": None, "created_at": None, "market_date": None,
                    "candidates_count": 0, "candidates": [],
                }
            cursor.execute(
                """
                SELECT symbol, company_name, sector, close_price, entry_price, target_price,
                       stop_loss_price, probability_score, ai_score, rank, expected_return_pct,
                       risk_reward_ratio, avg_volume, avg_turnover, volatility, rsi, momentum_5d,
                       momentum_20d, volume_ratio, trend_score, relative_strength_score,
                       breakout_score, risk_score, reasons
                FROM five_percent_strategy_candidates
                WHERE run_id = %s
                ORDER BY rank
                """,
                (run["id"],),
            )
            candidates = [dict(row) for row in cursor.fetchall()]
        return {
            **{key: value for key, value in run.items() if key != "id"},
            "candidates_count": len(candidates),
            "candidates": candidates,
        }

    def five_percent_strategy_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, run_id, created_at, market_date, strategy_version, model_version,
                       target_pct, stop_loss_pct, holding_days, probability_threshold,
                       initial_capital, max_candidates, status, metadata
                FROM five_percent_strategy_runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run = cursor.fetchone()
            if run is None:
                return None
            cursor.execute(
                """
                SELECT symbol, company_name, sector, close_price, entry_price, target_price,
                       stop_loss_price, probability_score, ai_score, rank, expected_return_pct,
                       risk_reward_ratio, avg_volume, avg_turnover, volatility, rsi, momentum_5d,
                       momentum_20d, volume_ratio, trend_score, relative_strength_score,
                       breakout_score, risk_score, reasons
                FROM five_percent_strategy_candidates
                WHERE run_id = %s
                ORDER BY rank
                """,
                (run["id"],),
            )
            candidates = [dict(row) for row in cursor.fetchall()]
        return {
            **{key: value for key, value in run.items() if key != "id"},
            "candidates_count": len(candidates),
            "candidates": candidates,
        }

    def five_percent_strategy_symbol_history(
        self, symbol: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT c.symbol, c.probability_score, c.ai_score, c.rank, c.entry_price,
                       c.target_price, c.stop_loss_price, c.reasons, c.created_at,
                       r.run_id, r.market_date
                FROM five_percent_strategy_candidates c
                JOIN five_percent_strategy_runs r ON r.id = c.run_id
                WHERE c.symbol = %s
                ORDER BY c.created_at DESC
                LIMIT %s
                """,
                (symbol, limit),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def save_five_percent_backtest_run(self, snapshot: dict[str, Any]) -> int:
        from psycopg.types.json import Jsonb

        trades = snapshot.get("trades", [])
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO five_percent_backtest_runs
                    (backtest_id, created_at, start_date, end_date, initial_capital,
                     final_capital, total_return_pct, target_pct, stop_loss_pct, holding_days,
                     total_trades, winning_trades, losing_trades, win_rate, average_win_pct,
                     average_loss_pct, max_drawdown_pct, profit_factor, longest_win_streak,
                     longest_loss_streak, assumptions, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    snapshot["backtest_id"], snapshot["created_at"], snapshot["start_date"],
                    snapshot["end_date"], snapshot["initial_capital"], snapshot["final_capital"],
                    snapshot["total_return_pct"], snapshot["target_pct"], snapshot["stop_loss_pct"],
                    snapshot["holding_days"], snapshot["total_trades"], snapshot["winning_trades"],
                    snapshot["losing_trades"], snapshot["win_rate"], snapshot.get("average_win_pct"),
                    snapshot.get("average_loss_pct"), snapshot.get("max_drawdown_pct"),
                    snapshot.get("profit_factor"), snapshot.get("longest_win_streak"),
                    snapshot.get("longest_loss_streak"), Jsonb(json_ready(snapshot.get("assumptions", {}))),
                    snapshot["status"],
                ),
            )
            backtest_pk = cursor.fetchone()[0]
            for trade in trades:
                cursor.execute(
                    """
                    INSERT INTO five_percent_backtest_trades
                        (backtest_id, symbol, entry_date, exit_date, entry_price, exit_price,
                         target_price, stop_loss_price, result, return_pct, capital_before,
                         capital_after, holding_days, exit_reason, probability_score, ai_score,
                         created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        backtest_pk, trade["symbol"], trade["entry_date"], trade.get("exit_date"),
                        trade["entry_price"], trade.get("exit_price"), trade["target_price"],
                        trade["stop_loss_price"], trade["result"], trade["return_pct"],
                        trade["capital_before"], trade["capital_after"], trade["holding_days"],
                        trade["exit_reason"], trade.get("probability_score"), trade.get("ai_score"),
                        snapshot["created_at"],
                    ),
                )
        return len(trades)

    def save_five_percent_paper_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO five_percent_paper_trades
                    (signal_id, symbol, entry_date, entry_price, target_price, stop_loss_price,
                     current_price, status, capital_before, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    trade.get("signal_id"), trade["symbol"], trade["entry_date"],
                    trade["entry_price"], trade["target_price"], trade["stop_loss_price"],
                    trade.get("current_price"), trade["status"], trade["capital_before"],
                    trade["created_at"], trade["updated_at"],
                ),
            )
            row = cursor.fetchone()
        return dict(row)

    def list_five_percent_paper_trades(
        self, status: str | None = None
    ) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        query = "SELECT * FROM five_percent_paper_trades"
        parameters: list[Any] = []
        if status is not None:
            query += " WHERE status = %s"
            parameters.append(status)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_five_percent_paper_trade(self, trade_id: int) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM five_percent_paper_trades WHERE id = %s", (trade_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def update_five_percent_paper_trade(
        self, trade_id: int, update: dict[str, Any]
    ) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        if not update:
            return self.get_five_percent_paper_trade(trade_id)
        columns = list(update.keys())
        assignments = ", ".join(f"{column} = %s" for column in columns)
        values = [update[column] for column in columns]
        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"UPDATE five_percent_paper_trades SET {assignments} WHERE id = %s RETURNING *",
                (*values, trade_id),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def save_bookmark(self, user_id: int, symbol: str, price: float) -> dict[str, Any]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO stock_bookmarks (user_id, symbol, bookmark_price, created_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (user_id, symbol)
                DO UPDATE SET bookmark_price = EXCLUDED.bookmark_price, created_at = now()
                RETURNING *
                """,
                (user_id, symbol, price),
            )
            row = cursor.fetchone()
        return dict(row)

    def list_bookmarks(self, user_id: int) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM stock_bookmarks WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def delete_bookmark(self, user_id: int, symbol: str) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM stock_bookmarks WHERE user_id = %s AND symbol = %s",
                (user_id, symbol),
            )
            deleted = cursor.rowcount > 0
        return deleted

    def close(self) -> None:
        # Connections are intentionally short-lived until pooling is introduced.
        return None
