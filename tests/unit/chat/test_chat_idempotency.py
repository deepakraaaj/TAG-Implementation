import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.platform.cache import cache
from app.services.chat import ChatService


class _CountingWorkflow:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, *_args, **_kwargs):
        self.calls += 1
        return {
            "messages": [type("M", (), {"content": f"response-{self.calls}"})()],
            "sql_query": "",
            "error": None,
            "workflow_payload": None,
            "token_usage": None,
        }


async def _collect_events(service: ChatService, request: ChatRequest):
    events = []
    async for chunk in service.generate_chat_stream(request):
        events.append(json.loads(chunk))
    return events


def test_idempotency_key_reuses_cached_terminal_response(monkeypatch):
    service = ChatService()
    workflow = _CountingWorkflow()
    store = {}

    async def _cache_get(key):
        return store.get(key)

    async def _cache_set(key, value, ttl=3600):
        store[key] = value
        return True

    async def _cache_delete(key):
        store.pop(key, None)

    monkeypatch.setattr(cache, "get", _cache_get)
    monkeypatch.setattr(cache, "set", _cache_set)
    monkeypatch.setattr(cache, "delete", _cache_delete)

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request_1 = ChatRequest(session_id="idem-s1", message="hello", metadata={}, idempotency_key="k-1")
        events_1 = asyncio.run(_collect_events(service, request_1))

        request_2 = ChatRequest(session_id="idem-s1", message="hello", metadata={}, idempotency_key="k-1")
        events_2 = asyncio.run(_collect_events(service, request_2))

        history = asyncio.run(service.history_store.load("idem-s1"))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert events_1[-1]["type"] == "result"
    assert events_2[-1]["type"] == "result"
    assert events_1[-1]["message"] == "response-1"
    assert events_2[-1]["message"] == "response-1"
    assert isinstance(events_1[-1].get("stage_timings_ms"), dict)
    assert isinstance(events_2[-1].get("stage_timings_ms"), dict)
    assert "total" in events_1[-1]["stage_timings_ms"]
    assert "total" in events_2[-1]["stage_timings_ms"]
    assert len(history) == 2
