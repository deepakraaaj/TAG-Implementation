import asyncio

from app.assistant.nodes.response_node import ResponseNode


def test_response_node_includes_filters_on_zero_rows():
    node = ResponseNode()
    state = {
        "sql_query": (
            "SELECT tt.id FROM task_transaction tt "
            "JOIN facility f ON tt.facility_id=f.id "
            "WHERE f.company_id='56942686' AND f.name='Ele unit _G Floor_Warehouse' "
            "AND assigned_user_id=11784578"
        ),
        "row_count": 0,
        "rows_preview": [],
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert "No records found for the exact filters" in msg
    assert "assigned_user_id=11784578" in msg
    assert "f.name='Ele unit _G Floor_Warehouse'" in msg
