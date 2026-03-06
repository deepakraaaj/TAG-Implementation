from app.domains.maintenance import rules
from app.domains.registry import DomainRegistry


def test_scheduler_flow_candidate_matches_schedule_phrasing():
    domain = DomainRegistry("maintenance")
    assert rules.is_flow_candidate(
        "schedule maintenance task for tomorrow",
        "scheduler_task_details",
        config=domain.config,
    )


def test_scheduler_flow_candidate_matches_create_task_phrasing():
    domain = DomainRegistry("maintenance")
    assert rules.is_flow_candidate(
        "create a task for nirmala",
        "scheduler_task_details",
        config=domain.config,
    )


def test_scheduler_flow_candidate_ignores_read_query_phrasing():
    domain = DomainRegistry("maintenance")
    assert not rules.is_flow_candidate(
        "show tasks for nirmala",
        "scheduler_task_details",
        config=domain.config,
    )


def test_resolve_flow_slot_prefill_parses_for_user_in_facility_phrase():
    domain = DomainRegistry("maintenance")
    payload = rules.resolve_flow_slot_prefill(
        "schedule a task for Soban in Developer Hu",
        "scheduler_task_details",
        "insert",
        {},
        allow_message_fallback=True,
        config=domain.config,
    )
    search = dict(payload.get("search") or {})
    values = dict(payload.get("values") or {})

    assert search.get("assigned_user") == "Soban"
    assert search.get("facility_id_or_name") == "Developer Hu"
    assert values.get("task_for") == "facility"


def test_resolve_flow_slot_prefill_merges_existing_search_with_message_fallback():
    domain = DomainRegistry("maintenance")
    payload = rules.resolve_flow_slot_prefill(
        "schedule a task for Soban in Developer Hu",
        "scheduler_task_details",
        "insert",
        {"assigned_user": "Soban"},
        allow_message_fallback=True,
        config=domain.config,
    )
    search = dict(payload.get("search") or {})

    assert search.get("assigned_user") == "Soban"
    assert search.get("facility_id_or_name") == "Developer Hu"
