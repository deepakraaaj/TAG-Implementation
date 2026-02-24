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


def test_response_node_handles_update_syntax_error_gracefully():
    node = ResponseNode()
    state = {
        "sql_query": "Update task status",
        "error": "(mysql.connector.errors.ProgrammingError) 1064 (42000): You have an error in your SQL syntax",
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert "under development" in msg
    assert "ProgrammingError" not in msg


def test_response_node_summarizes_total_and_shown_from_total_count_column():
    node = ResponseNode()
    preview = [
        {"_total_count": 132, "asset_id": 1, "name": "Pump-1"},
        {"_total_count": 132, "asset_id": 2, "name": "Pump-2"},
        {"_total_count": 132, "asset_id": 3, "name": "Pump-3"},
    ]
    state = {
        "sql_query": (
            "SELECT COUNT(*) OVER() AS _total_count, asset.id AS asset_id, asset.name "
            "FROM asset WHERE asset.company_id = 56942686 ORDER BY asset.id DESC LIMIT 500;"
        ),
        "row_count": 132,
        "rows_preview": preview,
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert msg == "Total 132 assets found. Showing 3."


def test_response_node_summarizes_total_and_shown_from_total_records_state():
    node = ResponseNode()
    preview = [
        {"asset_id": 1, "name": "Pump-1"},
        {"asset_id": 2, "name": "Pump-2"},
        {"asset_id": 3, "name": "Pump-3"},
        {"asset_id": 4, "name": "Pump-4"},
    ]
    state = {
        "sql_query": (
            "SELECT COUNT(*) OVER() AS _total_count, asset.id AS asset_id, asset.name "
            "FROM asset WHERE asset.company_id = 56942686 ORDER BY asset.id DESC LIMIT 500;"
        ),
        "row_count": 10,
        "rows_preview": preview,
        "total_records": 132,
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert msg == "Total 132 assets found. Showing 4."
