import asyncio

from app.assistant.nodes.response_node import ResponseNode


def test_response_node_shows_count_value_for_aggregate_result():
    node = ResponseNode()
    state = {
        "error": None,
        "sql_query": "SELECT COUNT(*) AS total_tasks FROM task_transaction WHERE DATE(scheduled_date)=CURDATE();",
        "row_count": 1,
        "rows_preview": [{"total_tasks": 55}],
        "metadata": {},
    }

    result = asyncio.run(node.run(state))
    assert result["messages"][-1].content == "Count: 55."
