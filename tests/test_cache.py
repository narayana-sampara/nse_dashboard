from nse_dashboard.infrastructure.cache import MemoryTtlCache, RedisTtlCache


def test_cache_returns_a_copy() -> None:
    cache = MemoryTtlCache()
    value = {"items": [1]}
    cache.set("key", value, ttl_seconds=30)

    cached = cache.get("key")
    assert cached is not None
    cached["items"].append(2)

    assert cache.get("key") == {"items": [1]}


def test_zero_ttl_is_not_cached() -> None:
    cache = MemoryTtlCache()
    cache.set("key", "value", ttl_seconds=0)
    assert cache.get("key") is None


def test_memory_cache_sets_value_only_when_absent() -> None:
    cache = MemoryTtlCache()

    assert cache.set_if_absent("key", "first", ttl_seconds=30) is True
    assert cache.set_if_absent("key", "second", ttl_seconds=30) is False
    assert cache.get("key") == "first"


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


def test_redis_cache_serializes_values_as_json() -> None:
    cache = RedisTtlCache.__new__(RedisTtlCache)
    cache._client = FakeRedisClient()
    cache.set("key", {"items": [1]}, ttl_seconds=30)

    cached = cache.get("key")
    cached["items"].append(2)

    assert cache.get("key") == {"items": [1]}
    assert cache.ping() is True
