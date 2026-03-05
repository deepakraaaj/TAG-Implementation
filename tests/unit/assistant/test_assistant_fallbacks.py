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


def test_router_fallback_report():
    assert RouterService.fallback("list reports") == "REPORT"


def test_router_fallback_report_name_without_report_keyword_stays_sql():
    assert (
        RouterService.fallback(
            "show pending tasks",
            sql_terms={"show", "tasks"},
            report_terms={"report", "pending tasks"},
        )
        == "SQL"
    )


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


def test_intent_analyze_with_usage_includes_recent_context(monkeypatch):
    service = object.__new__(IntentService)
    service.llm = object()
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"operation":"select","table":"facility","filters":{},"fields":{}}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(intent_service_module, "ainvoke_with_retry", _fake_llm)

    intent, usage = asyncio.run(
        service.analyze_with_usage(
            "what are they",
            metadata={
                "token_minimization": False,
                "pending_select_table": "facility",
                "_recent_conversation": [
                    {"role": "user", "content": "How many facilities don't have tasks today"},
                    {"role": "assistant", "content": "Found 90 records."},
                ],
            },
        )
    )
    assert intent.get("operation") == "select"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "Current Context (Last Table): facility" in captured_prompt["value"]
    assert "Recent Conversation Context:" in captured_prompt["value"]


def test_router_route_with_usage_uses_llm_for_greeting(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("hello"))
    assert route == "CHAT"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "User: hello" in captured_prompt["value"]


def test_router_route_with_usage_uses_llm_for_identity_query(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("who are you"))
    assert route == "CHAT"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "User: who are you" in captured_prompt["value"]


def test_router_referential_query_with_recent_context_uses_llm(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"show", "list", "task", "facility"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"route":"SQL"}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(
        service.route_with_usage(
            "what are they",
            metadata={
                "_recent_conversation": [
                    {"role": "user", "content": "How many facilities don't have tasks today"},
                    {"role": "assistant", "content": "Found 90 records."},
                ]
            },
        )
    )
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "Recent Conversation" in captured_prompt["value"]


def test_router_referential_query_without_context_still_uses_llm(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)
    route, usage = asyncio.run(service.route_with_usage("what are they"))
    assert route == "CHAT"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "User: what are they" in captured_prompt["value"]


def test_router_route_with_usage_uses_llm_for_report_query(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"work_item", "person"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports", "work item status summary"})
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"route":"REPORT"}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("show me the work item status summary report"))
    assert route == "REPORT"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "User: show me the work item status summary report" in captured_prompt["value"]


def test_router_llm_report_route_is_respected(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"select"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "pending tasks"})

    class _FakeResponse:
        content = '{"route":"REPORT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("can you pull pending items for my team today"))
    assert route == "REPORT"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_referential_followup_with_pending_context_forces_sql(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(
        service.route_with_usage(
            "what are they",
            metadata={
                "pending_select_table": "facility",
                "pending_select_negation": {
                    "subject_table": "facility",
                    "object_table": "task_transaction",
                    "date_scope": "today",
                },
                "_recent_conversation": [
                    {"role": "user", "content": "How many facilities don't have tasks today"},
                    {"role": "assistant", "content": "Found 90 records."},
                ],
            },
        )
    )
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_referential_followup_report_prediction_with_pending_context_forces_sql(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()

    class _FakeResponse:
        content = '{"route":"REPORT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(
        service.route_with_usage(
            "what are they",
            metadata={
                "pending_select_table": "facility",
                "pending_select_negation": {
                    "subject_table": "facility",
                    "object_table": "task_transaction",
                    "date_scope": "today",
                },
            },
        )
    )
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_fallback_referential_followup_with_pending_context_forces_sql(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()

    async def _failing_llm(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _failing_llm)

    route, usage = asyncio.run(
        service.route_with_usage(
            "what are they",
            metadata={"pending_select_table": "facility"},
        )
    )
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) == 0
