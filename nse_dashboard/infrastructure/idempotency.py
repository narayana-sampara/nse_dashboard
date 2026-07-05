from __future__ import annotations

import json
from threading import Lock
from typing import Any, Callable, Protocol, TypeVar
from uuid import uuid4

T = TypeVar("T")


class TaskAlreadyRunning(RuntimeError):
    pass


class IdempotencyStore(Protocol):
    def result(self, key: str) -> Any | None: ...

    def acquire(self, key: str, ttl_seconds: int) -> str | None: ...

    def complete(self, key: str, token: str, result: Any, ttl_seconds: int) -> None: ...

    def release(self, key: str, token: str) -> None: ...


class RedisIdempotencyStore:
    def __init__(self, url: str, socket_timeout: float = 2.0) -> None:
        from redis import Redis

        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
        )

    @staticmethod
    def _lock_key(key: str) -> str:
        return f"tasks:lock:{key}"

    @staticmethod
    def _result_key(key: str) -> str:
        return f"tasks:result:{key}"

    def result(self, key: str) -> Any | None:
        value = self._client.get(self._result_key(key))
        return None if value is None else json.loads(value)

    def acquire(self, key: str, ttl_seconds: int) -> str | None:
        token = str(uuid4())
        acquired = self._client.set(self._lock_key(key), token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    def complete(self, key: str, token: str, result: Any, ttl_seconds: int) -> None:
        self._client.set(
            self._result_key(key),
            json.dumps(result, separators=(",", ":")),
            ex=ttl_seconds,
        )
        self.release(key, token)

    def release(self, key: str, token: str) -> None:
        self._client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            self._lock_key(key),
            token,
        )


class MemoryIdempotencyStore:
    """Deterministic test implementation with the same ownership semantics."""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}
        self._locks: dict[str, str] = {}
        self._mutex = Lock()

    def result(self, key: str) -> Any | None:
        with self._mutex:
            return self._results.get(key)

    def acquire(self, key: str, ttl_seconds: int) -> str | None:
        del ttl_seconds
        with self._mutex:
            if key in self._locks:
                return None
            token = str(uuid4())
            self._locks[key] = token
            return token

    def complete(self, key: str, token: str, result: Any, ttl_seconds: int) -> None:
        del ttl_seconds
        with self._mutex:
            if self._locks.get(key) != token:
                raise RuntimeError("Idempotency lock ownership was lost")
            self._results[key] = result
            self._locks.pop(key, None)

    def release(self, key: str, token: str) -> None:
        with self._mutex:
            if self._locks.get(key) == token:
                self._locks.pop(key, None)


def execute_once(
    store: IdempotencyStore,
    key: str,
    lock_ttl_seconds: int,
    result_ttl_seconds: int,
    operation: Callable[[], T],
) -> T:
    existing = store.result(key)
    if existing is not None:
        return existing
    token = store.acquire(key, lock_ttl_seconds)
    if token is None:
        raise TaskAlreadyRunning(key)
    try:
        result = operation()
        store.complete(key, token, result, result_ttl_seconds)
        return result
    except Exception:
        store.release(key, token)
        raise
