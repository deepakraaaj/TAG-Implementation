import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService
from app.services.platform.cache import cache


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


def test_chat_cache_key_includes_history_content_and_ignores_trace_id():
    service = ChatService()
    history_one = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "response-a"},
    ]
    history_two = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "response-b"},
    ]
    request_one = ChatRequest(session_id="cache-s1", message="show status", metadata={"trace_id": "trace-1"})
    request_two = ChatRequest(session_id="cache-s1", message="show status", metadata={"trace_id": "trace-2"})

    key_one = service._chat_cache_key(request_one, history_one)
    key_two = service._chat_cache_key(request_two, history_one)
    key_three = service._chat_cache_key(request_one, history_two)

    assert key_one == key_two
    assert key_one != key_three


def test_request_fingerprint_changes_with_response_format():
    service = ChatService()
    request_one = ChatRequest(
        session_id="idem-format-s1",
        message="show status",
        metadata={"response_format": "json", "trace_id": "trace-1"},
        idempotency_key="idem-1",
    )
    request_two = ChatRequest(
        session_id="idem-format-s1",
        message="show status",
        metadata={"response_format": "toon", "trace_id": "trace-2"},
        idempotency_key="idem-1",
    )

    assert service._request_fingerprint(request_one) != service._request_fingerprint(request_two)


def test_loggable_metadata_redacts_sensitive_values():
    service = ChatService()

    safe = service._loggable_metadata(
        {
            "db_connection_string": "mysql://user:secret@db.example.com/app",
            "api_key": "top-secret",
            "user_id": "42",
            "trace_id": "trace-123",
        }
    )

    assert safe["db_connection_string"] == "[redacted]"
    assert safe["api_key"] == "[redacted]"
    assert safe["user_id"] == "42"
    assert safe["trace_id"] == "trace-123"


def test_generate_chat_stream_tolerates_cache_failures(monkeypatch):
    service = ChatService()
    workflow = _CountingWorkflow()

    async def _cache_get(_key):
        raise RuntimeError("cache unavailable")

    async def _cache_set(_key, _value, ttl=3600):
        raise RuntimeError("cache unavailable")

    async def _cache_delete(_key):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(cache, "get", _cache_get)
    monkeypatch.setattr(cache, "set", _cache_set)
    monkeypatch.setattr(cache, "delete", _cache_delete)

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(session_id="cache-down-s1", message="hello", metadata={})
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "ok"
    assert events[-1]["message"] == "response-1"
