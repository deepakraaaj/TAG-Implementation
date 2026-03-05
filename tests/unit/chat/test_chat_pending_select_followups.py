import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService
from app.services.platform.cache import cache


class _CaptureWorkflow:
    def __init__(self):
        self.calls = 0
        self.last_user_message = ""
        self.last_metadata = {}

    async def ainvoke(self, inputs, *_args, **_kwargs):
        self.calls += 1
        messages = inputs.get("messages") or []
        self.last_user_message = str(messages[-1].content) if messages else ""
        self.last_metadata = dict(inputs.get("metadata") or {})
        return {
            "messages": [type("M", (), {"content": "ok"})()],
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


def test_negation_pending_context_does_not_rewrite_freeform_followup(monkeypatch):
    service = ChatService()
    workflow = _CaptureWorkflow()
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

    session_id = "pending-neg-followup-s1"
    store[service._pending_select_key(session_id)] = {
        "table": "facility",
        "negation": {
            "subject_table": "facility",
            "object_table": "task_transaction",
            "date_scope": "today",
        },
    }

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(session_id=session_id, message="who are you", metadata={})
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert workflow.last_user_message == "who are you"
    assert events[-1]["status"] == "ok"


def test_non_negation_pending_context_still_rewrites_filter_followup(monkeypatch):
    service = ChatService()
    workflow = _CaptureWorkflow()
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

    session_id = "pending-filter-followup-s1"
    store[service._pending_select_key(session_id)] = {
        "table": "facility",
        "filters": {"status": "Pending"},
    }

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(session_id=session_id, message="priority=High", metadata={})
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert workflow.last_user_message.startswith("show facility ")
    assert "status=Pending" in workflow.last_user_message
    assert "priority=High" in workflow.last_user_message
    assert events[-1]["status"] == "ok"


def test_non_negation_pending_context_does_not_rewrite_conversational_followup(monkeypatch):
    service = ChatService()
    workflow = _CaptureWorkflow()
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

    session_id = "pending-conversation-followup-s1"
    store[service._pending_select_key(session_id)] = {
        "table": "facility",
        "filters": {"status": "Pending"},
    }

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(session_id=session_id, message="who are you", metadata={})
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert workflow.last_user_message == "who are you"
    assert events[-1]["status"] == "ok"


def test_chat_service_attaches_last_five_turns_context(monkeypatch):
    service = ChatService()
    workflow = _CaptureWorkflow()
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

    session_id = "recent-context-window-s1"
    for i in range(1, 7):
        asyncio.run(service.history_store.append_turn(session_id, f"user-{i}", f"assistant-{i}"))

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(session_id=session_id, message="what are they", metadata={})
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    recent = workflow.last_metadata.get("_recent_conversation")
    assert workflow.calls == 1
    assert isinstance(recent, list)
    assert len(recent) == 10  # 5 turns x (user + assistant)
    contents = [str(item.get("content")) for item in recent if isinstance(item, dict)]
    assert "user-1" not in contents
    assert "user-2" in contents
    assert "assistant-6" in contents
    assert isinstance(workflow.last_metadata.get("_recent_conversation_text"), str)
    assert events[-1]["status"] == "ok"
