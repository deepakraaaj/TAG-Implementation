import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService


class _FailingWorkflow:
    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError("workflow exploded")


async def _collect_events(service: ChatService, request: ChatRequest):
    events = []
    async for chunk in service.generate_chat_stream(request):
        events.append(json.loads(chunk))
    return events


def test_stream_emits_terminal_result_when_workflow_missing():
    service = ChatService()
    request = ChatRequest(session_id="s-missing", message="hello", metadata={})
    original = lifespan.workflow
    lifespan.workflow = None
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "error"
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "error"
    assert events[-1]["session_id"] == "s-missing"
    assert str(events[-1].get("llm_model", "")).strip()
    assert str(events[-1].get("trace_id", "")).strip()
    assert isinstance(events[-1].get("stage_timings_ms"), dict)
    assert "total" in events[-1]["stage_timings_ms"]


def test_stream_emits_terminal_result_when_workflow_raises():
    service = ChatService()
    request = ChatRequest(session_id="s-error", message="hello", metadata={})
    original = lifespan.workflow
    lifespan.workflow = _FailingWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "error"
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "error"
    assert events[-1]["session_id"] == "s-error"
    assert str(events[-1].get("llm_model", "")).strip()
    assert str(events[-1].get("trace_id", "")).strip()
    assert isinstance(events[-1].get("stage_timings_ms"), dict)
    assert "total" in events[-1]["stage_timings_ms"]


def test_final_response_normalizes_path_like_llm_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "/home/user/.cache/llmfit/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf")

    payload = ChatService._build_final_response(
        session_id="s-model-name",
        message="hello",
    )

    assert payload["llm_model"] == "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"


class _SuccessWorkflow:
    async def ainvoke(self, *_args, **_kwargs):
        return {
            "messages": [type("M", (), {"content": "Found 2 record(s)."})()],
            "sql_query": "SELECT id, name FROM asset WHERE company_id=1 LIMIT 2;",
            "row_count": 2,
            "rows_preview": [{"id": 1, "name": "Pump-1"}, {"id": 2, "name": "Pump-2"}],
            "token_usage": None,
            "error": None,
        }


def test_stream_includes_toon_token_summary_in_token_and_result_message():
    service = ChatService()
    request = ChatRequest(
        session_id="s-toon-summary",
        message="list assets",
        metadata={"response_format": "toon"},
    )
    original = lifespan.workflow
    lifespan.workflow = _SuccessWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "token"
    assert "Token estimate for preview" not in str(events[0].get("content", ""))
    assert events[-1]["type"] == "result"
    assert "Token estimate for preview" not in str(events[-1].get("message", ""))
    assert "Token estimate for preview" in str(events[-1].get("sql", {}).get("rows_preview_token_summary", ""))
    assert events[-1].get("sql", {}).get("rows_preview_encoding") == "toon"


def test_stream_includes_token_summary_in_default_json_mode():
    service = ChatService()
    request = ChatRequest(
        session_id="s-token-summary-default",
        message="list assets",
        metadata={},
    )
    original = lifespan.workflow
    lifespan.workflow = _SuccessWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    assert events[0]["type"] == "token"
    assert "Token estimate for preview" not in str(events[0].get("content", ""))
    assert events[-1]["type"] == "result"
    assert "Token estimate for preview" not in str(events[-1].get("message", ""))
    assert "Token estimate for preview" in str(events[-1].get("sql", {}).get("rows_preview_token_summary", ""))


def test_stream_includes_endpoint_pre_stream_timing_when_available():
    service = ChatService()
    request = ChatRequest(
        session_id="s-endpoint-pre-stream",
        message="list assets",
        metadata={"_endpoint_pre_stream_ms": 120.5, "_user_lookup_ms": 45.2},
    )
    original = lifespan.workflow
    lifespan.workflow = _SuccessWorkflow()
    try:
        events = asyncio.run(_collect_events(service, request))
    finally:
        lifespan.workflow = original

    timings = events[-1].get("stage_timings_ms", {})
    assert float(timings.get("endpoint_pre_stream", 0)) == 120.5
    assert float(timings.get("user_lookup", 0)) == 45.2
