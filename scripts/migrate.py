from __future__ import annotations

import os
from pathlib import Path

import psycopg


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cursor.execute("SELECT name FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}
            for path in sorted(migration_dir.glob("*.sql")):
                if path.name in applied:
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                print(f"applied {path.name}")


if __name__ == "__main__":
    main()
