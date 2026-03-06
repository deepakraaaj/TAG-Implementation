import re

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


class _FakeDomainFlowRulesCreateTask:
    @staticmethod
    def is_flow_candidate(message: str, table: str) -> bool:
        msg = str(message or "").lower()
        if table != "scheduler_task_details":
            return False
        if "schedule" in msg or "scheduled" in msg:
            return True
        return bool(re.search(r"\b(create|add|assign|new)\b", msg) and re.search(r"\b(task|tasks)\b", msg))


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


def test_select_flow_binding_for_message_upgrades_create_task_phrase_to_insert_flow():
    bindings = [
        {"flow_id": "create_schedule", "table": "scheduler_task_details", "operation": "insert"},
    ]
    selected = ChatService._select_flow_binding_for_message(
        bindings,
        _FakeDomainFlowRulesCreateTask(),
        "create a task for nirmala",
        "task_transaction",
        "select",
    )
    assert isinstance(selected, dict)
    assert selected.get("flow_id") == "create_schedule"
    assert selected.get("table") == "scheduler_task_details"
    assert selected.get("operation") == "insert"


def test_extract_flow_prefill_hints_from_message():
    hints = ChatService._extract_flow_prefill_hints_from_message(
        "create a task for nirmala",
        "scheduler_task_details",
        "insert",
    )
    assert hints.get("assigned_user") == "nirmala"


def test_extract_scheduler_prefill_hints_from_assign_phrase():
    hints = ChatService._extract_flow_prefill_hints_from_message(
        "assign task for vijaya",
        "scheduler_task_details",
        "insert",
    )
    assert hints.get("assigned_user") == "vijaya"
    assert not hints.get("facility_id_or_name")


def test_extract_scheduler_prefill_hints_from_facility_and_assignee_phrase():
    hints = ChatService._extract_flow_prefill_hints_from_message(
        "create a task for Developers Hub for soban",
        "scheduler_task_details",
        "insert",
    )
    assert hints.get("facility_id_or_name") == "Developers Hub"
    assert hints.get("assigned_user") == "soban"


def test_extract_scheduler_prefill_hints_from_single_facility_phrase():
    hints = ChatService._extract_flow_prefill_hints_from_message(
        "create a task for Developer Hub",
        "scheduler_task_details",
        "insert",
    )
    assert hints.get("facility_id_or_name") == "Developer Hub"
    assert not hints.get("assigned_user")


def test_extract_scheduler_prefill_hints_from_for_user_in_facility_phrase():
    hints = ChatService._extract_flow_prefill_hints_from_message(
        "schedule a task for Soban in Developer Hu",
        "scheduler_task_details",
        "insert",
    )
    assert hints.get("assigned_user") == "Soban"
    assert hints.get("facility_id_or_name") == "Developer Hu"


def test_flow_prefill_search_hints_for_create_task_phrase():
    hints = ChatService._flow_prefill_search_hints(
        "create a task for nirmala",
        "scheduler_task_details",
        "insert",
        {},
    )
    assert hints.get("assigned_user") == "nirmala"


def test_flow_prefill_search_hints_for_single_facility_phrase():
    hints = ChatService._flow_prefill_search_hints(
        "create a task for Developer Hub",
        "scheduler_task_details",
        "insert",
        {},
    )
    assert hints.get("facility_id_or_name") == "Developer Hub"
    assert not hints.get("assigned_user")


def test_flow_prefill_search_hints_for_facility_and_assignee_phrase():
    hints = ChatService._flow_prefill_search_hints(
        "create a task for Developers Hub for soban",
        "scheduler_task_details",
        "insert",
        {},
    )
    assert hints.get("facility_id_or_name") == "Developers Hub"
    assert hints.get("assigned_user") == "soban"


def test_flow_prefill_values_sets_task_for_facility_when_facility_hint_exists():
    values = ChatService._flow_prefill_values(
        "create a task for Developers Hub for soban",
        "scheduler_task_details",
        "insert",
        {},
    )
    assert values.get("task_for") == "facility"


def test_normalize_scheduler_flow_fields_maps_common_aliases():
    normalized = ChatService._normalize_flow_fields(
        "scheduler_task_details",
        {
            "assignee": "soban",
            "facility": "Developer Hub",
            "task": "Dusting",
            "scheduler": "79",
        }
    )
    assert normalized.get("assigned_user") == "soban"
    assert normalized.get("facility_id_or_name") == "Developer Hub"
    assert normalized.get("task_description_id") == "Dusting"
    assert normalized.get("sche_details_id") == "79"


def test_flow_prefill_search_hints_uses_initial_fields_without_message_fallback():
    hints = ChatService._flow_prefill_search_hints(
        "create a task",
        "scheduler_task_details",
        "insert",
        {"facility_id_or_name": "Developer Hub", "assigned_user": "soban"},
        allow_message_fallback=False,
    )
    assert hints.get("facility_id_or_name") == "Developer Hub"
    assert hints.get("assigned_user") == "soban"


def test_flow_prefill_search_hints_merges_existing_fields_and_message_hints():
    hints = ChatService._flow_prefill_search_hints(
        "schedule a task for Soban in Developer Hu",
        "scheduler_task_details",
        "insert",
        {"assigned_user": "Soban"},
        allow_message_fallback=True,
    )
    assert hints.get("assigned_user") == "Soban"
    assert hints.get("facility_id_or_name") == "Developer Hu"


def test_flow_prefill_values_infers_task_for_from_initial_fields_without_message_fallback():
    values = ChatService._flow_prefill_values(
        "create a task",
        "scheduler_task_details",
        "insert",
        {"asset_id_or_name": "Generator A"},
        allow_message_fallback=False,
    )
    assert values.get("task_for") == "asset"
