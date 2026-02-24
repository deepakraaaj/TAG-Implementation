import asyncio
import json

from app.api.v1.endpoints import chat as chat_endpoint
from app.schemas.chat import ChatRequest


async def _collect_events(response):
    events = []
    async for chunk in response.body_iterator:
        payload = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in payload.splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def test_endpoint_stream_emits_terminal_result_on_internal_stream_error(monkeypatch):
    async def _failing_stream(_request):
        yield json.dumps({"type": "token", "content": "partial"}) + "\n"
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _failing_stream)

    request = ChatRequest(session_id="endpoint-stream-error", message="hello", metadata={})
    response = asyncio.run(chat_endpoint.query_tag(request, req=None, x_user_context=None))
    events = asyncio.run(_collect_events(response))

    assert events[0]["type"] == "token"
    assert events[-2]["type"] == "error"
    assert events[-1]["type"] == "result"
    assert events[-1]["status"] == "error"
    assert events[-1]["session_id"] == "endpoint-stream-error"
    assert str(events[-1].get("trace_id", "")).strip()


def test_endpoint_stream_uses_supplied_trace_id_on_terminal_result(monkeypatch):
    async def _failing_stream(_request):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _failing_stream)

    request = ChatRequest(session_id="endpoint-trace-error", message="hello", metadata={})
    response = asyncio.run(chat_endpoint.query_tag(request, req=None, x_user_context=None, x_trace_id="trace-123"))
    events = asyncio.run(_collect_events(response))

    assert events[-1]["type"] == "result"
    assert events[-1]["trace_id"] == "trace-123"
