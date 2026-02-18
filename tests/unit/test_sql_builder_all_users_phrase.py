from app.assistant.nodes.sql_builder_node import SQLBuilderNode


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
