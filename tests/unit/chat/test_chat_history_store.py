import asyncio
from copy import deepcopy
import json

from app.services.chat import ChatHistoryStore
from app.services.platform.cache import cache


def test_history_store_appends_and_trims(monkeypatch):
    memory = {}

    async def fake_get(key):
        return memory.get(key)

    async def fake_set(key, value, ttl=3600):
        memory[key] = value
        return True

    monkeypatch.setattr(cache, "get", fake_get)
    monkeypatch.setattr(cache, "set", fake_set)

    store = ChatHistoryStore(ttl_seconds=60, max_messages=4)

    asyncio.run(store.append_turn("session-x", "u1", "a1"))
    asyncio.run(store.append_turn("session-x", "u2", "a2"))
    asyncio.run(store.append_turn("session-x", "u3", "a3"))

    history = asyncio.run(store.load("session-x"))

    assert len(history) == 4
    assert history[0] == {"role": "user", "content": "u2"}
    assert history[-1] == {"role": "assistant", "content": "a3"}


def test_history_store_normalizes_before_saving(monkeypatch):
    memory = {}

    async def fake_get(key):
        return deepcopy(memory.get(key))

    async def fake_set(key, value, ttl=3600):
        memory[key] = deepcopy(value)
        return True

    monkeypatch.setattr(cache, "get", fake_get)
    monkeypatch.setattr(cache, "set", fake_set)

    store = ChatHistoryStore(ttl_seconds=60, max_messages=3)
    asyncio.run(
        store.save(
            "session-y",
            [
                {"role": "USER", "content": " hello "},
                {"role": "assistant", "content": " world "},
                {"role": "system", "content": "ignore"},
                {"role": "assistant", "content": ""},
            ],
        )
    )

    history = asyncio.run(store.load("session-y"))

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]


def test_history_store_append_turn_is_safe_under_concurrency(monkeypatch):
    memory = {}

    async def fake_get(key):
        await asyncio.sleep(0.01)
        return deepcopy(memory.get(key))

    async def fake_set(key, value, ttl=3600):
        await asyncio.sleep(0.01)
        memory[key] = deepcopy(value)
        return True

    monkeypatch.setattr(cache, "get", fake_get)
    monkeypatch.setattr(cache, "set", fake_set)

    store = ChatHistoryStore(ttl_seconds=60, max_messages=10)

    async def run_test():
        await asyncio.gather(
            store.append_turn("session-z", "u1", "a1"),
            store.append_turn("session-z", "u2", "a2"),
        )
        return await store.load("session-z")

    history = asyncio.run(run_test())

    assert len(history) == 4
    assert {(item["role"], item["content"]) for item in history} == {
        ("user", "u1"),
        ("assistant", "a1"),
        ("user", "u2"),
        ("assistant", "a2"),
    }


class _PipelineCacheBackend:
    def __init__(self):
        self._store = {}
        self._redis = self._FakeRedis(self._store)

    @staticmethod
    def generate_key(prefix, *args):
        return f"{prefix}:{':'.join(str(arg) for arg in args)}"

    async def get(self, key):
        raw_value = self._store.get(key)
        if raw_value is None:
            return None
        return json.loads(raw_value)

    async def set(self, key, value, ttl=3600):
        self._store[key] = json.dumps(value)
        return True

    async def delete(self, key):
        self._store.pop(key, None)

    class _FakeRedis:
        def __init__(self, store):
            self.store = store

        def pipeline(self, transaction=True):
            return _PipelineCacheBackend._FakePipeline(self.store)

    class _FakePipeline:
        def __init__(self, store):
            self.store = store
            self.queued = None
            self.transaction_started = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def watch(self, _key):
            return None

        async def get(self, key):
            return self.store.get(key)

        def multi(self):
            self.transaction_started = True

        def setex(self, key, ttl, value):
            assert self.transaction_started is True
            self.queued = (key, ttl, value)
            return self

        async def execute(self):
            key, _ttl, value = self.queued
            self.store[key] = value
            return [True]


def test_history_store_transactional_append_uses_pipeline_without_awaiting_setex():
    backend = _PipelineCacheBackend()
    store = ChatHistoryStore(cache_backend=backend, ttl_seconds=60, max_messages=10)

    history = asyncio.run(store.append_turn("session-r", "u1", "a1"))

    assert history == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_history_store_clamps_ttl_to_positive(monkeypatch):
    captured = {}

    async def fake_get(key):
        return None

    async def fake_set(key, value, ttl=3600):
        captured["ttl"] = ttl
        return True

    monkeypatch.setattr(cache, "get", fake_get)
    monkeypatch.setattr(cache, "set", fake_set)

    store = ChatHistoryStore(ttl_seconds=0, max_messages=4)
    asyncio.run(store.save("session-ttl", [{"role": "user", "content": "hello"}]))

    assert captured["ttl"] == 1
