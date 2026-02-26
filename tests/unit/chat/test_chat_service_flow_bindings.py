from app.domains.registry import DomainRegistry
from app.services.chat import ChatService


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


class _FakeDomainFlowRules:
    @staticmethod
    def is_flow_candidate(message: str, table: str) -> bool:
        msg = str(message or "").lower()
        if table == "scheduler_task_details":
            return "schedule" in msg or "scheduled" in msg
        return False


def test_select_flow_binding_for_message_upgrades_schedule_phrase_to_insert_flow():
    bindings = [
        {"flow_id": "create_schedule", "table": "scheduler_task_details", "operation": "insert"},
    ]
    selected = ChatService._select_flow_binding_for_message(
        bindings,
        _FakeDomainFlowRules(),
        "Schedule a task",
        "task_transaction",
        "select",
    )
    assert isinstance(selected, dict)
    assert selected.get("flow_id") == "create_schedule"
    assert selected.get("table") == "scheduler_task_details"
    assert selected.get("operation") == "insert"


def test_select_flow_binding_for_message_does_not_upgrade_show_scheduled_query():
    bindings = [
        {"flow_id": "create_schedule", "table": "scheduler_task_details", "operation": "insert"},
    ]
    selected = ChatService._select_flow_binding_for_message(
        bindings,
        _FakeDomainFlowRules(),
        "Show scheduled maintenance for next week",
        "task_transaction",
        "select",
    )
    assert selected is None


def test_select_flow_binding_for_message_does_not_upgrade_can_you_show_scheduled_query():
    bindings = [
        {"flow_id": "create_schedule", "table": "scheduler_task_details", "operation": "insert"},
    ]
    selected = ChatService._select_flow_binding_for_message(
        bindings,
        _FakeDomainFlowRules(),
        "Can you show scheduled maintenance for next week?",
        "task_transaction",
        "select",
    )
    assert selected is None
