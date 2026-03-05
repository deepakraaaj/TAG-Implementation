from tests.e2e.scenarios import (
    assert_chat_query_executes_report_through_active_graph,
    assert_chat_query_rejects_prompt_injection_with_safe_message,
    assert_chat_query_supports_pagination_and_summary_followups,
)


def test_chat_query_supports_pagination_and_summary_followups(app_client):
    assert_chat_query_supports_pagination_and_summary_followups(app_client)


def test_chat_query_rejects_prompt_injection_with_safe_message(app_client):
    assert_chat_query_rejects_prompt_injection_with_safe_message(app_client)


def test_chat_query_executes_report_through_active_graph(app_client):
    assert_chat_query_executes_report_through_active_graph(app_client)
