import asyncio

from app.services.chat_support.history_store import ChatHistoryStore
from app.services.cache import cache


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
