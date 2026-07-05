from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncContextManager, Protocol

STREAM_CHANNELS = frozenset({"signals", "alerts", "options"})


def event_message(channel: str, event_type: str, data: Any) -> dict[str, Any]:
    if channel not in STREAM_CHANNELS:
        raise ValueError(f"Unsupported stream channel: {channel}")
    return {
        "channel": channel,
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


class EventBroker(Protocol):
    async def publish(self, channel: str, event_type: str, data: Any) -> int: ...

    def subscribe(self, channel: str) -> AsyncContextManager[AsyncIterator[dict[str, Any]]]: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class MemoryEventBroker:
    """Process-local broker used when Redis is not configured and in tests."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, event_type: str, data: Any) -> int:
        message = event_message(channel, event_type, data)
        async with self._lock:
            queues = tuple(self._subscribers[channel])
        for queue in queues:
            queue.put_nowait(deepcopy(message))
        return len(queues)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        if channel not in STREAM_CHANNELS:
            raise ValueError(f"Unsupported stream channel: {channel}")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._subscribers[channel].add(queue)

        async def messages() -> AsyncIterator[dict[str, Any]]:
            while True:
                yield await queue.get()

        try:
            yield messages()
        finally:
            async with self._lock:
                self._subscribers[channel].discard(queue)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisEventBroker:
    """Async Redis pub/sub broker shared by all API and worker processes."""

    def __init__(self, url: str, socket_timeout: float = 2.0, prefix: str = "nse:stream") -> None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError("Install the 'redis' package to use REDIS_URL") from exc
        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            # Subscriptions are intentionally idle between market events.
            socket_timeout=None,
        )
        self._prefix = prefix.rstrip(":")

    def _name(self, channel: str) -> str:
        if channel not in STREAM_CHANNELS:
            raise ValueError(f"Unsupported stream channel: {channel}")
        return f"{self._prefix}:{channel}"

    async def publish(self, channel: str, event_type: str, data: Any) -> int:
        payload = json.dumps(event_message(channel, event_type, data), separators=(",", ":"))
        return int(await self._client.publish(self._name(channel), payload))

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(self._name(channel))

        async def messages() -> AsyncIterator[dict[str, Any]]:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield json.loads(message["data"])

        try:
            yield messages()
        finally:
            await pubsub.aclose()

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def close(self) -> None:
        await self._client.aclose()


class RedisEventPublisher:
    """Synchronous publisher for Celery workers."""

    def __init__(self, url: str, socket_timeout: float = 2.0, prefix: str = "nse:stream") -> None:
        from redis import Redis

        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
        )
        self._prefix = prefix.rstrip(":")

    def publish(self, channel: str, event_type: str, data: Any) -> int:
        if channel not in STREAM_CHANNELS:
            raise ValueError(f"Unsupported stream channel: {channel}")
        payload = json.dumps(event_message(channel, event_type, data), separators=(",", ":"))
        return int(self._client.publish(f"{self._prefix}:{channel}", payload))

    def close(self) -> None:
        self._client.close()
