from __future__ import annotations

from typing import Any

from nse_dashboard.core.security import hash_password, verify_password


class AuthRepository:
    """User accounts and per-user menu permissions stored in PostgreSQL."""

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

    def ensure_admin(self, username: str, password: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")
            if cursor.fetchone() is not None:
                return
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT (username) DO NOTHING
                """,
                (username, hash_password(password)),
            )

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = %s",
                (username,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3]}

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, role FROM users WHERE id = %s", (user_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {"id": row[0], "username": row[1], "role": row[2]}

    def get_permissions(self, user_id: int) -> list[str]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT menu_key FROM user_menu_permissions WHERE user_id = %s", (user_id,)
            )
            return [row[0] for row in cursor.fetchall()]

    def list_users_with_permissions(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, username, role FROM users ORDER BY username")
            users = [
                {"id": row[0], "username": row[1], "role": row[2], "permissions": []}
                for row in cursor.fetchall()
            ]
            cursor.execute("SELECT user_id, menu_key FROM user_menu_permissions")
            permissions_by_user: dict[int, list[str]] = {}
            for user_id, menu_key in cursor.fetchall():
                permissions_by_user.setdefault(user_id, []).append(menu_key)
            for user in users:
                user["permissions"] = permissions_by_user.get(user["id"], [])
            return users

    def create_user(self, username: str, password: str, role: str = "user") -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                RETURNING id, username, role
                """,
                (username, hash_password(password), role),
            )
            row = cursor.fetchone()
            return {"id": row[0], "username": row[1], "role": row[2]}

    def set_permissions(self, user_id: int, menu_keys: list[str]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_menu_permissions WHERE user_id = %s", (user_id,))
            for menu_key in menu_keys:
                cursor.execute(
                    "INSERT INTO user_menu_permissions (user_id, menu_key) VALUES (%s, %s)",
                    (user_id, menu_key),
                )

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return verify_password(password, password_hash)

    def close(self) -> None:
        return None

    def ping(self) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
