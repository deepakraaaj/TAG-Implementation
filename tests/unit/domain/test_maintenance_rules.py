from app.domains.maintenance import rules


def test_scheduler_flow_candidate_matches_schedule_phrasing():
    assert rules.is_flow_candidate("schedule maintenance task for tomorrow", "scheduler_task_details")


def test_scheduler_flow_candidate_matches_create_task_phrasing():
    assert rules.is_flow_candidate("create a task for nirmala", "scheduler_task_details")


def test_scheduler_flow_candidate_ignores_read_query_phrasing():
    assert not rules.is_flow_candidate("show tasks for nirmala", "scheduler_task_details")
