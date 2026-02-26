import asyncio

from app.assistant.engine.intent.intent_service import IntentService
from app.assistant.engine import intent_service as intent_service_module
from app.assistant.engine.router.router_service import RouterService
from app.assistant.engine import router_service as router_service_module


def test_router_fallback_sql():
    assert RouterService.fallback("list users") == "SQL"


def test_router_fallback_chat():
    assert RouterService.fallback("hello there") == "CHAT"


def test_router_fallback_sql_from_manifest_alias():
    assert RouterService.fallback("employees assigned today") == "SQL"


def test_intent_fallback_insert():
    payload = IntentService.fallback("create asset named Pump")
    assert payload["operation"] == "insert"


def test_intent_fallback_update():
    payload = IntentService.fallback("update task status to done")
    assert payload["operation"] == "update"


def test_intent_fallback_schedule_defaults_to_select():
    payload = IntentService.fallback("Schedule a task")
    assert payload["operation"] == "select"


def test_intent_analyze_with_usage_skips_llm_for_simple_query(monkeypatch):
    service = object.__new__(IntentService)
    service.llm = object()

    async def _unexpected_llm(*_args, **_kwargs):
        raise AssertionError("LLM call should be skipped for simple query in minimization mode")

    monkeypatch.setattr(intent_service_module, "ainvoke_with_retry", _unexpected_llm)

    intent, usage = asyncio.run(
        service.analyze_with_usage("list assets", metadata={"token_minimization": True})
    )
    assert intent.get("operation") == "select"
    assert int(usage.get("llm_calls", 0)) == 0
    assert int(usage.get("llm_calls_skipped", 0)) >= 1


def test_router_route_with_usage_skips_llm_for_greeting(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()

    async def _unexpected_llm(*_args, **_kwargs):
        raise AssertionError("LLM classifier should be skipped for clear greeting")

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _unexpected_llm)

    route, usage = asyncio.run(service.route_with_usage("hello"))
    assert route == "CHAT"
    assert int(usage.get("llm_calls", 0)) == 0
    assert int(usage.get("llm_calls_skipped", 0)) >= 1
