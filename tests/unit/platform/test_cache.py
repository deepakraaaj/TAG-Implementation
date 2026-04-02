import asyncio

from app.services.platform.cache import RedisCache


class _FailingRedis:
    def __init__(self):
        self.closed = False

    async def ping(self):
        raise RuntimeError("redis down")

    async def aclose(self):
        self.closed = True


def test_redis_cache_falls_back_to_in_memory_store_when_connect_fails():
    failing_client = _FailingRedis()
    cache = RedisCache(
        redis_url="redis://cache",
        redis_client_factory=lambda *_args, **_kwargs: failing_client,
        _singleton=False,
    )

    asyncio.run(cache.connect())

    assert cache.using_fallback() is True
    assert asyncio.run(cache.ping()) is True
    assert asyncio.run(cache.set("demo:key", {"value": 7}, ttl=60)) is True
    assert asyncio.run(cache.get("demo:key")) == {"value": 7}
    assert asyncio.run(cache.delete("demo:key")) == 1
    assert asyncio.run(cache.get("demo:key")) is None
    assert failing_client.closed is True

    asyncio.run(cache.close())
