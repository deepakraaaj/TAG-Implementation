import asyncio

from app.domains.registry import DomainRegistry
from app.assistant.engine.response.response_intelligence import ResponseIntelligence
from app.assistant.nodes.core.chat_node import ChatNode
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
    assert payload["fields"]["status"] == "Done"


def test_intent_fallback_delete():
    payload = IntentService.fallback("delete everything")
    assert payload["operation"] == "delete"


def test_intent_fallback_extracts_update_id_and_status():
    payload = IntentService.fallback("update task #123 status to completed")
    assert payload["operation"] == "update"
    assert payload["fields"]["id"] == "123"
    assert payload["fields"]["status"] == "Completed"


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


def test_intent_analyze_with_usage_extracts_update_fields_when_llm_is_skipped(monkeypatch):
    service = object.__new__(IntentService)
    service.llm = object()

    async def _unexpected_llm(*_args, **_kwargs):
        raise AssertionError("LLM call should be skipped for simple update query in minimization mode")

    monkeypatch.setattr(intent_service_module, "ainvoke_with_retry", _unexpected_llm)

    intent, usage = asyncio.run(
        service.analyze_with_usage(
            "update task #123 status to completed",
            metadata={"token_minimization": True},
        )
    )
    assert intent.get("operation") == "update"
    assert intent.get("fields", {}).get("id") == "123"
    assert intent.get("fields", {}).get("status") == "Completed"
    assert int(usage.get("llm_calls", 0)) == 0


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


def test_intent_analyze_with_usage_force_llm_bypasses_minimization(monkeypatch):
    service = object.__new__(IntentService)
    service.llm = object()

    class _FakeResponse:
        content = '{"operation":"select","table":"asset","filters":{},"fields":{}}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(intent_service_module, "ainvoke_with_retry", _fake_llm)
    intent, usage = asyncio.run(
        service.analyze_with_usage(
            "list assets",
            metadata={"token_minimization": True, "_intent_force_llm": True},
        )
    )
    assert intent.get("table") == "asset"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_intent_analyze_with_usage_includes_field_extraction_guidance(monkeypatch):
    service = object.__new__(IntentService)
    service.llm = object()
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"operation":"insert","table":"scheduler_task_details","filters":{},"fields":{"assigned_user":"soban"}}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(intent_service_module, "ainvoke_with_retry", _fake_llm)
    intent, usage = asyncio.run(
        service.analyze_with_usage(
            "assign task for soban",
            metadata={
                "token_minimization": False,
                "_intent_fields_hint": "Use keys: assigned_user, facility_id_or_name",
            },
        )
    )
    assert intent.get("operation") == "insert"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "Field Extraction Guidance:" in captured_prompt["value"]
    assert "assigned_user" in captured_prompt["value"]


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


def test_router_exact_report_alias_routes_to_report_without_report_keyword(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"show", "task", "tasks"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports", "task status"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("task status"))
    assert route == "REPORT"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_report_route_is_downgraded_when_heuristic_is_not_report(monkeypatch):
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
    assert route == "CHAT"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_report_route_is_downgraded_for_plain_sql_query(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"show", "task", "tasks", "user", "assignee"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "pending tasks"})

    class _FakeResponse:
        content = '{"route":"REPORT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("show tasks for nirmala"))
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_chat_route_is_upgraded_for_short_sql_lookup(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"show", "list", "task", "tasks", "status", "pending"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("pending tasks list"))
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_chat_route_is_upgraded_for_status_lookup(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"task", "tasks", "status"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("task status"))
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_chat_route_is_upgraded_for_mapping_lookup(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"user", "users", "location", "locations"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("Which users are mapped to which locations?"))
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_chat_route_is_upgraded_by_semantic_retrieval(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    service.semantic_retriever = type(
        "_Retriever",
        (),
        {
            "route_min_score": 0.45,
            "search": lambda self, _query, **_kwargs: [
                {
                    "kind": "special_query",
                    "artifact_id": "vehicle_overspeed_ranking",
                    "candidate_tables": ["vehicle", "vts_exception"],
                    "score": 0.92,
                }
            ],
        },
    )()
    service.domain_provider = lambda: type(
        "_Domain",
        (),
        {"get_config_section": staticmethod(lambda _section: {})},
    )()
    monkeypatch.setattr(service, "_sql_terms", lambda: set())
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("which truck overspeeded the most"))
    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_llm_chat_route_is_upgraded_for_domain_special_query(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    service.domain_provider = DomainRegistry.get_current_domain
    monkeypatch.setattr(service, "_sql_terms", lambda: set())
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    with DomainRegistry.use_domain("vts"):
        route, usage = asyncio.run(service.route_with_usage("which truck reported as many times overspeeded"))

    assert route == "SQL"
    assert int(usage.get("llm_calls", 0)) >= 1


def test_router_keeps_conceptual_task_question_in_chat(monkeypatch):
    service = object.__new__(RouterService)
    service.llm = object()
    monkeypatch.setattr(service, "_sql_terms", lambda: {"task", "tasks"})
    monkeypatch.setattr(service, "_report_terms", lambda: {"report", "reports"})

    class _FakeResponse:
        content = '{"route":"CHAT"}'

    async def _fake_llm(*_args, **_kwargs):
        return _FakeResponse()

    monkeypatch.setattr(router_service_module, "ainvoke_with_retry", _fake_llm)

    route, usage = asyncio.run(service.route_with_usage("what is a task"))
    assert route == "CHAT"
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


def test_chat_node_help_response_uses_reasoning_engine_style():
    node = ChatNode()

    result = asyncio.run(node.run({"messages": [type("M", (), {"content": "who are you"})()]}))
    content = str(result["messages"][0].content)

    assert "Domain scope:" in content
    assert "Behavior:" in content
    assert "Examples:" in content
    assert "**" not in content


def test_response_intelligence_prefers_domain_knowledge_for_help_output():
    class _Domain:
        name = "warehouse_ops"
        manifest = {"tables": {}}

        @staticmethod
        def get_capabilities():
            return {}

        @staticmethod
        def get_entity_behavior_config():
            return {}

        @staticmethod
        def get_domain_knowledge_config():
            return {
                "scope": "warehouse operations including orders, staff, and sites",
                "primary_entities": ["orders", "staff", "sites"],
                "business_terms": {},
                "example_queries": ["show open orders", "list warehouse staff"],
                "workflows": [
                    {
                        "workflow_id": "create_order",
                        "label": "Create Order",
                        "table": "order",
                        "operation": "insert",
                        "trigger_phrases": ["create order"],
                        "required_fields": ["title"],
                        "reasoning": "Core warehouse action",
                        "confidence": 95,
                    }
                ],
                "reasoning_profile": {
                    "name": "ClearTM canonical AI reasoning",
                    "behavior_summary": (
                        "Direct answer first, one clarification if needed, and abstain instead of guessing when validated evidence is missing."
                    ),
                    "rules": ["frame only"],
                    "response_modes": {"default": "direct answer"},
                    "evidence_sources": ["sql_rowset"],
                    "clarification_policy": "Ask one clarification question when blocked.",
                    "abstention_policy": "If evidence is missing, abstain.",
                },
            }

    intelligence = ResponseIntelligence(domain_provider=lambda: _Domain(), llm=None)

    content = intelligence.get_help_response()

    assert "Domain scope: warehouse operations including orders, staff, and sites." in content
    assert "Behavior: Direct answer first, one clarification if needed" in content
    assert "Main entities: orders, staff, sites." in content
    assert "Suggested actions: Create Order." in content
    assert "- show open orders" in content
