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


class _PendingSelectWorkflow:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, *_args, **_kwargs):
        self.calls += 1
        return {
            "messages": [type("M", (), {"content": "negation-count"})()],
            "sql_query": (
                "SELECT COUNT(*) AS facilities_without_tasks_today "
                "FROM facility f WHERE f.company_id = 1 "
                "AND f.id NOT IN ("
                "SELECT DISTINCT tt.facility_id FROM task_transaction tt "
                "WHERE DATE(tt.scheduled_date) = CURDATE());"
            ),
            "error": None,
            "workflow_payload": None,
            "pending_select": {
                "table": "facility",
                "negation": {
                    "subject_table": "facility",
                    "object_table": "task_transaction",
                    "date_scope": "today",
                },
            },
            "row_count": 1,
            "rows_preview": [{"facilities_without_tasks_today": 90}],
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


def test_idempotency_key_does_not_replay_for_different_request_payload(monkeypatch):
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
        request_1 = ChatRequest(session_id="idem-s2", message="hello", metadata={}, idempotency_key="k-2")
        events_1 = asyncio.run(_collect_events(service, request_1))

        request_2 = ChatRequest(session_id="idem-s2", message="show assets", metadata={}, idempotency_key="k-2")
        events_2 = asyncio.run(_collect_events(service, request_2))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 2
    assert events_1[-1]["message"] == "response-1"
    assert events_2[-1]["message"] == "response-2"


def test_idempotency_replay_preserves_pending_select_context(monkeypatch):
    service = ChatService()
    workflow = _PendingSelectWorkflow()
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
        req1 = ChatRequest(
            session_id="idem-neg-s1",
            message="how many facilities without tasks today",
            metadata={},
            idempotency_key="neg-1",
        )
        events_1 = asyncio.run(_collect_events(service, req1))

        req2 = ChatRequest(
            session_id="idem-neg-s1",
            message="how many facilities without tasks today",
            metadata={},
            idempotency_key="neg-1",
        )
        events_2 = asyncio.run(_collect_events(service, req2))
        pending_state = asyncio.run(service._load_pending_select_state("idem-neg-s1"))
    finally:
        lifespan.workflow = original

    assert workflow.calls == 1
    assert events_1[-1].get("pending_select", {}).get("table") == "facility"
    assert events_2[-1].get("pending_select", {}).get("table") == "facility"
    assert isinstance(pending_state, dict)
    assert pending_state.get("table") == "facility"
    assert isinstance((pending_state or {}).get("negation"), dict)


def test_idempotent_legacy_payload_infers_pending_select_from_sql(monkeypatch):
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

    legacy_payload = {
        "type": "result",
        "session_id": "idem-legacy-s1",
        "status": "ok",
        "message": "legacy-negation",
        "workflow": None,
        "sql": {
            "ran": True,
            "cached": False,
            "query": (
                "SELECT COUNT(*) AS facilities_without_tasks_today "
                "FROM facility f WHERE f.company_id = 1 "
                "AND f.id NOT IN ("
                "SELECT DISTINCT tt.facility_id FROM task_transaction tt "
                "WHERE DATE(tt.scheduled_date) = CURDATE());"
            ),
            "row_count": 1,
            "rows_preview": [{"facilities_without_tasks_today": 90}],
        },
        "token_usage": None,
        "provider_used": "tag_backend",
    }
    idempotent_key = service._idempotency_cache_key("idem-legacy-s1", "legacy-1")
    store[idempotent_key] = legacy_payload

    original = lifespan.workflow
    lifespan.workflow = workflow
    try:
        request = ChatRequest(
            session_id="idem-legacy-s1",
            message="how many facilities without tasks today",
            metadata={},
            idempotency_key="legacy-1",
        )
        events = asyncio.run(_collect_events(service, request))
        pending_state = asyncio.run(service._load_pending_select_state("idem-legacy-s1"))
    finally:
        lifespan.workflow = original

    assert events[-1]["message"] == "legacy-negation"
    assert workflow.calls == 0
    assert isinstance(pending_state, dict)
    assert pending_state.get("table") == "facility"
    negation = pending_state.get("negation") or {}
    assert negation.get("subject_table") == "facility"
    assert negation.get("object_table") == "task_transaction"
    assert negation.get("date_scope") == "today"
