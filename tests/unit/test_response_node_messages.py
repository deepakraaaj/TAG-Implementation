import asyncio

from app.assistant.nodes.response_node import ResponseNode


def test_response_node_found_records_message_has_no_preview():
    node = ResponseNode()
    state = {
        "sql_query": "SELECT status FROM task_transaction WHERE status='Pending'",
        "row_count": 10,
        "rows_preview": [{"status": "Pending", "id": 1}],
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert msg == "Found 10 record(s)."
    assert "Preview" not in msg
