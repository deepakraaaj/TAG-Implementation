import asyncio

from app.assistant.nodes.sql_builder_node import SQLBuilderNode


def test_sql_builder_node_passthrough_for_raw_select_sql():
    node = SQLBuilderNode()
    sql = "SELECT id FROM task_transaction WHERE assigned_user_id=11784788 AND scheduled_date='2026-02-18' LIMIT 100;"
    state = {"messages": [type("M", (), {"content": sql})()], "metadata": {"company_id": "56942686"}, "intent": {}}

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == sql


def test_sql_builder_node_does_not_treat_natural_update_phrase_as_sql():
    assert SQLBuilderNode._looks_like_sql_statement("Update task status") is False
