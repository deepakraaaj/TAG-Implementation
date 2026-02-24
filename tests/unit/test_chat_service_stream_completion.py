import asyncio
import json

from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService


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
    assert str(events[-1].get("trace_id", "")).strip()
    assert isinstance(events[-1].get("stage_timings_ms"), dict)
    assert "total" in events[-1]["stage_timings_ms"]
