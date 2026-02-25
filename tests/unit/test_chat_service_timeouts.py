import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService


class _SlowWorkflow:
    async def ainvoke(self, *_args, **_kwargs):
        await asyncio.sleep(1.1)
        return {"messages": [type("M", (), {"content": "ok"})()]}


class _NoopWorkflow:
    async def ainvoke(self, *_args, **_kwargs):
        return {"messages": [type("M", (), {"content": "ok"})()]}


class _SlowSQLExecutor:
    async def run(self, *_args, **_kwargs):
        await asyncio.sleep(1.1)
        return {"row_count": 0, "rows_preview": []}


async def _collect_events(service: ChatService, request: ChatRequest):
    events = []
    async for chunk in service.generate_chat_stream(request):
        events.append(json.loads(chunk))
    return events


def test_stream_emits_terminal_result_when_workflow_times_out():
    service = ChatService()
    service.workflow_timeout_seconds = 1

    request = ChatRequest(session_id="s-timeout", message="hello", metadata={})
    original = lifespan.workflow
    lifespan.workflow = _SlowWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "error"
    assert "Workflow execution timed out" in events[0]["message"]
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "error"


def test_stream_emits_terminal_result_when_sql_load_more_times_out(monkeypatch):
    service = ChatService()
    service.sql_timeout_seconds = 1
    service.flow_engine.sql_executor = _SlowSQLExecutor()

    async def _empty_history(_session_id):
        return []

    async def _no_flow(_session_id):
        return None

    async def _last_select(_session_id):
        return {"sql": "SELECT id FROM task_transaction", "offset": 0, "limit": 20}

    monkeypatch.setattr(service, "_load_history", _empty_history)
    monkeypatch.setattr(service, "_load_flow_state", _no_flow)
    monkeypatch.setattr(service, "_load_pending_select_state", _no_flow)
    monkeypatch.setattr(service, "_load_last_select_state", _last_select)

    request = ChatRequest(session_id="s-sql-timeout", message="load more", metadata={})
    original = lifespan.workflow
    lifespan.workflow = _NoopWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "error"
    assert "SQL execution timed out" in events[0]["message"]
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "error"
    assert events[-1]["sql"]["ran"] is True
