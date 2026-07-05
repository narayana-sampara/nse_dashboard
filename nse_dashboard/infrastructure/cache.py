from __future__ import annotations

from copy import deepcopy
from threading import Lock
from time import monotonic
import json
from typing import Any, Protocol


class TtlCache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    def set_if_absent(self, key: str, value: Any, ttl_seconds: int) -> bool: ...

    def delete(self, key: str) -> None: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


class MemoryTtlCache:
    """Process-local cache for tests and deployments that do not configure Redis."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= monotonic():
                self._values.pop(key, None)
                return None
            return deepcopy(value)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._values[key] = (monotonic() + ttl_seconds, deepcopy(value))

    def set_if_absent(self, key: str, value: Any, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return False
        with self._lock:
            existing = self._values.get(key)
            if existing is not None:
                expires_at, _ = existing
                if expires_at > monotonic():
                    return False
                self._values.pop(key, None)
            self._values[key] = (monotonic() + ttl_seconds, deepcopy(value))
            return True

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


class RedisTtlCache:
    """JSON cache backed by Redis and shared across API processes."""

    def __init__(self, url: str, socket_timeout: float = 2.0) -> None:
        try:
            from redis import Redis
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError("Install the 'redis' package to use REDIS_URL") from exc
        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
        )

    def get(self, key: str) -> Any | None:
        value = self._client.get(key)
        return None if value is None else json.loads(value)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            self._client.set(key, json.dumps(value, separators=(",", ":")), ex=ttl_seconds)

    def set_if_absent(self, key: str, value: Any, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return False
        return bool(
            self._client.set(
                key,
                json.dumps(value, separators=(",", ":")),
                ex=ttl_seconds,
                nx=True,
            )
        )

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def close(self) -> None:
        self._client.close()
