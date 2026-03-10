from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


def test_normalized_filters_do_not_infer_assignee_from_for_phrase():
    query = "today's tasks for Ele unit _G Floor_Warehouse facility"
    filters = SQLBuilderNode._normalized_user_filters({}, query)
    assert "assignee" not in filters


def test_normalized_filters_clear_user_filters_for_all_users_phrase():
    query = "today's tasks for Ele unit _G Floor_Warehouse facility for all user"
    filters = SQLBuilderNode._normalized_user_filters({"assignee": "Ele"}, query)
    assert "assignee" not in filters
    assert "assigned_user_id" not in filters


def test_requests_all_users_phrase_detection():
    assert SQLBuilderNode._requests_all_users("for all users")
    assert SQLBuilderNode._requests_all_users("show tasks for everyone")
    assert not SQLBuilderNode._requests_all_users("assigned to nirmala")


def test_task_autorun_context_all_users_facility_date():
    filters = {"scheduled_date": "today", "facility_name": "Ele unit _G Floor_Warehouse"}
    assert SQLBuilderNode._has_task_autorun_context(filters)


def test_task_autorun_context_assignee_and_date():
    filters = {"scheduled_date": "today", "assignee": "Nirmala S"}
    assert SQLBuilderNode._has_task_autorun_context(filters)


def test_requests_self_tasks_detection():
    assert SQLBuilderNode._requests_self_tasks("show my tasks today")
    assert SQLBuilderNode._requests_self_tasks("tasks for me")
    assert not SQLBuilderNode._requests_self_tasks("task status today")


def test_normalized_filters_extracts_assignee_from_tasks_for_name():
    filters = SQLBuilderNode._normalized_user_filters({}, "tasks for Nirmala today")
    assert filters.get("assignee") == "Nirmala"


def test_normalized_filters_extracts_assignee_with_explicit_iso_date_clause():
    filters = SQLBuilderNode._normalized_user_filters({}, "show pending tasks for Nirmala dated on 2026-01-30")
    assert filters.get("assignee") == "Nirmala"
    assert filters.get("scheduled_date") == "2026-01-30"


def test_normalized_filters_extracts_assignee_for_me():
    filters = SQLBuilderNode._normalized_user_filters({}, "tasks for me")
    assert filters.get("assignee") == "me"


def test_normalized_filters_do_not_infer_assignee_from_dont_contraction():
    filters = SQLBuilderNode._normalized_user_filters({}, "which user don't have task today")
    assert "assignee" not in filters
    assert filters.get("scheduled_date") == "today"


def test_today_your_tasks_option_parses_current_user_alias():
    filters = SQLBuilderNode._normalized_user_filters({}, "scheduled_date=today, assigned_to=current_user")
    assert filters.get("scheduled_date") == "today"
    assert filters.get("assigned_to") == "current_user"
