import asyncio
import base64
import json
from types import SimpleNamespace

from app.api.v1.endpoints import chat as chat_endpoint
from app.apps.registry import AppConfig, AppRegistry
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


def _encode_context(payload):
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


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
    assert str(events[-1].get("llm_model", "")).strip()
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
    assert str(events[-1].get("llm_model", "")).strip()
    assert events[-1]["trace_id"] == "trace-123"


def test_endpoint_replaces_invalid_user_name_with_db_name(monkeypatch):
    captured = {}
    lookup_calls = []

    async def _capture_stream(request):
        captured["metadata"] = dict(request.metadata or {})
        captured["user_id"] = str(request.user_id or "")
        yield json.dumps({"type": "token", "content": "ok"}) + "\n"

    def _fake_user_lookup(user_id, db_url=None):
        assert db_url is None or isinstance(db_url, str)
        lookup_calls.append(str(user_id))
        return {"user_name": "Deepak"}

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)
    monkeypatch.setattr(chat_endpoint.user_service, "get_user_info", _fake_user_lookup)

    context = {
        "user_id": "11784212",
        "company_id": "56942516",
        "company_name": "Kritilabs",
        "user_name": "Kritilabs",
    }
    request = ChatRequest(session_id="endpoint-user-name-fix", message="show my tasks today", metadata={})
    response = asyncio.run(
        chat_endpoint.query_tag(request, req=None, x_user_context=_encode_context(context))
    )
    asyncio.run(_collect_events(response))

    assert lookup_calls == ["11784212"]
    assert captured["user_id"] == "11784212"
    assert captured["metadata"].get("user_name") == "Deepak"


def test_endpoint_keeps_valid_user_name_without_lookup(monkeypatch):
    captured = {}
    lookup_calls = []

    async def _capture_stream(request):
        captured["metadata"] = dict(request.metadata or {})
        yield json.dumps({"type": "token", "content": "ok"}) + "\n"

    def _fake_user_lookup(user_id, db_url=None):
        assert db_url is None or isinstance(db_url, str)
        lookup_calls.append(str(user_id))
        return {"user_name": "ShouldNotBeUsed"}

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)
    monkeypatch.setattr(chat_endpoint.user_service, "get_user_info", _fake_user_lookup)

    request = ChatRequest(
        session_id="endpoint-user-name-keep",
        message="show my tasks today",
        user_id="11784212",
        metadata={"user_name": "Vinothini V", "company_name": "Kritilabs"},
    )
    response = asyncio.run(chat_endpoint.query_tag(request, req=None, x_user_context=None))
    asyncio.run(_collect_events(response))

    assert lookup_calls == []
    assert captured["metadata"].get("user_name") == "Vinothini V"


def test_endpoint_injects_response_format_from_header(monkeypatch):
    captured = {}

    async def _capture_stream(request):
        captured["metadata"] = dict(request.metadata or {})
        yield json.dumps({"type": "token", "content": "ok"}) + "\n"

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)

    request = ChatRequest(session_id="endpoint-response-format", message="list assets", metadata={})
    response = asyncio.run(
        chat_endpoint.query_tag(
            request,
            req=None,
            x_user_context=None,
            x_response_format="TOON",
        )
    )
    asyncio.run(_collect_events(response))

    assert captured["metadata"].get("response_format") == "toon"


def test_endpoint_sets_no_buffering_headers(monkeypatch):
    async def _capture_stream(_request):
        yield json.dumps({"type": "token", "content": "ok"}) + "\n"

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)

    request = ChatRequest(session_id="endpoint-headers", message="hello", metadata={})
    response = asyncio.run(chat_endpoint.query_tag(request, req=None, x_user_context=None))

    assert response.headers.get("x-accel-buffering") == "no"
    assert "no-cache" in str(response.headers.get("cache-control", "")).lower()


def test_endpoint_non_stream_returns_terminal_result_json(monkeypatch):
    async def _capture_stream(_request):
        yield json.dumps({"type": "token", "content": "partial"}) + "\n"
        yield json.dumps(
            {
                "type": "result",
                "session_id": "endpoint-json-mode",
                "status": "ok",
                "message": "done",
                "llm_model": "demo-model",
                "trace_id": "trace-json-mode",
            }
        ) + "\n"

    monkeypatch.setattr(chat_endpoint.chat_service, "generate_chat_stream", _capture_stream)

    request = ChatRequest(session_id="endpoint-json-mode", message="hello", metadata={})
    response = asyncio.run(
        chat_endpoint.query_tag(
            request,
            req=None,
            x_user_context=None,
            stream=False,
        )
    )

    payload = json.loads(response.body.decode("utf-8"))
    assert payload["type"] == "result"
    assert payload["status"] == "ok"
    assert payload["session_id"] == "endpoint-json-mode"
    assert payload["llm_model"] == "demo-model"


def test_apply_app_config_injects_default_metadata():
    registry = AppRegistry(
        {
            "REMP": AppConfig(
                display_name="FITS",
                database_url="mysql://demo/fits",
                domain="REMP",
                default_metadata={"company_id": 56942686},
            )
        },
        default_app_id="REMP",
    )
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(app_registry=registry))))
    metadata = {}

    app_id, config = chat_endpoint._apply_app_config(req, metadata, "REMP")

    assert app_id == "REMP"
    assert config is not None
    assert metadata["company_id"] == 56942686
    assert metadata["domain_name"] == "REMP"
