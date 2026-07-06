from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nse_dashboard.core.security import hash_password  # noqa: E402


def _default_users() -> list[tuple[str, str, str]]:
    return [
        (
            os.environ.get("ADMIN_BOOTSTRAP_USERNAME", "admin"),
            os.environ["ADMIN_BOOTSTRAP_PASSWORD"],
            "admin",
        ),
        (
            os.environ["SEED_USER1_USERNAME"],
            os.environ["SEED_USER1_PASSWORD"],
            "user",
        ),
        (
            os.environ["SEED_USER2_USERNAME"],
            os.environ["SEED_USER2_PASSWORD"],
            "user",
        ),
    ]


def main() -> None:
    import psycopg

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    users = _default_users()

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for username, password, role in users:
                cursor.execute("SELECT 1 FROM users WHERE username = %s", (username,))
                if cursor.fetchone() is not None:
                    print(f"skip {username}: already exists")
                    continue
                cursor.execute(
                    """
                    INSERT INTO users (username, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (username, hash_password(password), role),
                )
                print(f"created {username} ({role})")


if __name__ == "__main__":
    main()
