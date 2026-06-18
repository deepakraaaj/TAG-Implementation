from app.domains.REMP import rules
from app.domains.registry import DomainRegistry


def test_create_task_flow_candidate_matches_create_task_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "Create a maintenance task",
        "task_transaction",
        config=domain.config,
    )


def test_create_task_flow_candidate_matches_tasks_creation_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "tasks creation",
        "task_transaction",
        config=domain.config,
    )


def test_create_task_flow_candidate_ignores_read_query_phrasing():
    domain = DomainRegistry("REMP")
    assert not rules.is_flow_candidate(
        "show pending maintenance tasks",
        "task_transaction",
        config=domain.config,
    )


def test_assign_task_flow_candidate_matches_assignment_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "assign task to john",
        "task_transaction",
        config=domain.config,
    )


def test_update_task_status_flow_candidate_matches_status_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "mark task as completed",
        "task_transaction",
        config=domain.config,
    )


def test_update_checklist_flow_candidate_matches_checklist_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "complete checklist for this task",
        "check_list_transaction",
        config=domain.config,
    )


def test_create_schedule_flow_candidate_matches_schedule_phrasing():
    domain = DomainRegistry("REMP")
    assert rules.is_flow_candidate(
        "create recurring maintenance schedule",
        "scheduler_task_details",
        config=domain.config,
    )
