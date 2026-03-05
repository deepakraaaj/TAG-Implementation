import asyncio
from fnmatch import fnmatch

from app.services.platform.cache_service import CacheService


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.closed = False

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                deleted += 1
        return deleted

    async def scan_iter(self, match):
        for key in list(self.store):
            if fnmatch(key, match):
                yield key

    async def info(self, _section):
        return {"used_memory": 1024 * 1024}

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


def test_generate_cache_key_is_stable_and_order_independent():
    fake_redis = _FakeRedis()
    service = CacheService(
        enabled=True,
        default_ttl=60,
        redis_url="redis://cache",
        redis_client_factory=lambda _url: fake_redis,
    )

    key_one = service.generate_cache_key(
        "summary",
        7,
        1,
        50,
        filters={"status": ["open", "new"], "priority": {"gte": 2}},
        sort={"field": "created_at", "direction": "desc"},
    )
    key_two = service.generate_cache_key(
        "summary",
        7,
        1,
        50,
        sort={"direction": "desc", "field": "created_at"},
        filters={"priority": {"gte": 2}, "status": ["open", "new"]},
    )
    key_three = service.generate_cache_key(
        "summary",
        7,
        1,
        50,
        sort={"direction": "asc", "field": "created_at"},
        filters={"priority": {"gte": 2}, "status": ["open", "new"]},
    )

    assert key_one == key_two
    assert key_one != key_three


def test_generate_cache_key_normalizes_set_values():
    fake_redis = _FakeRedis()
    service = CacheService(
        enabled=True,
        default_ttl=60,
        redis_url="redis://cache",
        redis_client_factory=lambda _url: fake_redis,
    )

    key_one = service.generate_cache_key("summary", 7, 1, 50, statuses={"new", "open"})
    key_two = service.generate_cache_key("summary", 7, 1, 50, statuses={"open", "new"})

    assert key_one == key_two


def test_invalidate_report_handles_zero_company_id():
    fake_redis = _FakeRedis()
    fake_redis.store = {
        "report:usage:0:1:50:a": "{}",
        "report:usage:0:2:50:b": "{}",
        "report:usage:1:1:50:c": "{}",
        "report:other:0:1:50:d": "{}",
    }
    service = CacheService(
        enabled=True,
        default_ttl=60,
        redis_url="redis://cache",
        redis_client_factory=lambda _url: fake_redis,
    )

    deleted = asyncio.run(service.invalidate_report("usage", company_id=0))

    assert deleted == 2
    assert "report:usage:1:1:50:c" in fake_redis.store
    assert "report:other:0:1:50:d" in fake_redis.store


def test_close_releases_report_cache_client():
    fake_redis = _FakeRedis()
    service = CacheService(
        enabled=True,
        default_ttl=60,
        redis_url="redis://cache",
        redis_client_factory=lambda _url: fake_redis,
    )

    asyncio.run(service.close())

    assert fake_redis.closed is True
    assert service.redis_client is None
