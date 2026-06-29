import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService
from app.services.platform.cache import cache


def test_parse_load_more_request_with_offset():
    limit, offset = ChatService._parse_load_more_request("Show the next 15 records for the previous query. (Offset: 20)")
    assert limit == 15
    assert offset == 20


def test_parse_load_more_request_defaults():
    limit, offset = ChatService._parse_load_more_request("load more")
    assert limit == 20
    assert offset is None


def test_apply_limit_offset_rewrites_existing_limit():
    sql = "SELECT id, status FROM task_transaction WHERE company_id=1 AND status=2 LIMIT 100;"
    out = ChatService._apply_limit_offset(sql, 15, 20)
    assert out.endswith("LIMIT 15 OFFSET 20;")
    assert "LIMIT 100" not in out


def test_bounded_page_limit_clamps_to_max():
    service = ChatService()
    service.max_page_size = 25
    assert service._bounded_page_limit(None) == 20
    assert service._bounded_page_limit(10) == 10
    assert service._bounded_page_limit(999) == 25


class _NoopWorkflow:
    async def ainvoke(self, *_args, **_kwargs):
        return {"messages": [type("M", (), {"content": "ok"})()]}


class _PagingSQLExecutor:
    def __init__(self):
        self.queries = []

    async def run(self, payload):
        sql = str((payload or {}).get("sql_query", "")).strip()
        self.queries.append(sql)
        if "OFFSET 20" in sql.upper():
            rows = [{"id": i, "name": f"Facility-{i}"} for i in range(21, 36)]
        elif "OFFSET 35" in sql.upper():
            rows = [{"id": i, "name": f"Facility-{i}"} for i in range(36, 51)]
        else:
            rows = []
        return {
            "error": None,
            "row_count": len(rows),
            "rows_preview": list(rows),
            "sql_result": json.dumps(rows),
        }


async def _collect_events(service: ChatService, request: ChatRequest):
    events = []
    async for chunk in service.generate_chat_stream(request):
        events.append(json.loads(chunk))
    return events


def test_load_more_returns_cumulative_rows_and_advances_offset(monkeypatch):
    service = ChatService()
    executor = _PagingSQLExecutor()
    service.flow_engine.sql_executor = executor
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

    session_id = "load-more-progress-s1"
    store[service._last_select_key(session_id)] = {
        "sql": "SELECT id, name FROM facility WHERE company_id = 1",
        "offset": 20,
        "limit": 20,
        "row_count": 90,
        "total_records": 90,
        "loaded_count": 20,
        "loaded_rows": [{"id": i, "name": f"Facility-{i}"} for i in range(1, 21)],
    }

    original = lifespan.workflow
    lifespan.workflow = _NoopWorkflow()
    try:
        req_1 = ChatRequest(
            session_id=session_id,
            message="Show the next 15 records for the previous query. (Offset: 20)",
            metadata={},
        )
        events_1 = asyncio.run(_collect_events(service, req_1))

        req_2 = ChatRequest(
            session_id=session_id,
            message="Show the next 15 records for the previous query. (Offset: 20)",
            metadata={},
        )
        events_2 = asyncio.run(_collect_events(service, req_2))
    finally:
        lifespan.workflow = original

    assert len(executor.queries) >= 2
    assert "LIMIT 15 OFFSET 20" in executor.queries[0].upper()
    assert "LIMIT 15 OFFSET 35" in executor.queries[1].upper()

    result_1 = events_1[-1]
    assert result_1["status"] == "ok"
    assert result_1["sql"]["row_count"] == 15
    assert len(result_1["sql"]["rows_preview"]) == 15
    assert "Showing 15 more record(s)." in str(result_1.get("message", ""))

    result_2 = events_2[-1]
    assert result_2["status"] == "ok"
    assert result_2["sql"]["row_count"] == 15
    assert len(result_2["sql"]["rows_preview"]) == 15
    assert "Showing 15 more record(s)." in str(result_2.get("message", ""))
