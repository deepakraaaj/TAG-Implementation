from app.domains.registry import DomainRegistry
from app.services.chat_service import ChatService


def test_domain_flow_bindings_reads_explicit_bindings():
    domain = DomainRegistry("maintenance")
    bindings = ChatService._domain_flow_bindings(domain)
    assert any(
        item.get("flow_id") == "create_schedule"
        and item.get("table") == "scheduler_task_details"
        and item.get("operation") == "insert"
        for item in bindings
    )


def test_select_flow_binding_matches_table_and_operation():
    bindings = [
        {"flow_id": "a", "table": "task_transaction", "operation": "insert"},
        {"flow_id": "b", "table": "scheduler_task_details", "operation": "insert"},
    ]
    flow_id = ChatService._select_flow_binding(bindings, "scheduler_task_details", "insert")
    assert flow_id == "b"


def test_domain_flow_bindings_falls_back_to_flows_enabled():
    class _FakeDomain:
        config = {"flows_enabled": ["legacy_flow"]}

    bindings = ChatService._domain_flow_bindings(_FakeDomain())
    assert bindings == [{"flow_id": "legacy_flow", "table": "", "operation": ""}]
