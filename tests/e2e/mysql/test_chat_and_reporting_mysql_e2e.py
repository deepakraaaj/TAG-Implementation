from tests.e2e.scenarios import (
    assert_chat_query_executes_report_through_active_graph,
    assert_chat_query_rejects_prompt_injection_with_safe_message,
    assert_chat_query_supports_pagination_and_summary_followups,
)


def test_mysql_chat_query_supports_pagination_and_summary_followups(mysql_app_client):
    assert_chat_query_supports_pagination_and_summary_followups(
        mysql_app_client,
        session_id="e2e-mysql-chat-pagination",
    )


def test_mysql_chat_query_rejects_prompt_injection_with_safe_message(mysql_app_client):
    assert_chat_query_rejects_prompt_injection_with_safe_message(
        mysql_app_client,
        session_id="e2e-mysql-injection",
    )


def test_mysql_chat_query_executes_report_through_active_graph(mysql_app_client):
    assert_chat_query_executes_report_through_active_graph(
        mysql_app_client,
        session_id="e2e-mysql-report",
    )
