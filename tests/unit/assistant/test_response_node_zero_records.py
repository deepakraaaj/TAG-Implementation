import asyncio

from app.assistant.nodes.core.response_node import ResponseNode
from app.domains.registry import DomainRegistry


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
    assert "No records found for" in msg
    assert "Ele unit _G Floor_Warehouse" in msg


def test_response_node_hides_id_filters_from_zero_rows_message():
    node = ResponseNode()
    state = {
        "sql_query": (
            "SELECT tt.id FROM task_transaction tt "
            "JOIN facility f ON tt.facility_id=f.id "
            "LEFT JOIN user u ON tt.assigned_user_id=u.id "
            "WHERE f.company_id='56942686' "
            "AND tt.assigned_user_id=11784578 "
            "AND DATE(tt.scheduled_date)=CURDATE() "
            "AND LOWER(TRIM(CONCAT(COALESCE(u.first_name,''), ' ', COALESCE(u.last_name,'')))) LIKE LOWER('%Vinothini V%')"
        ),
        "row_count": 0,
        "rows_preview": [],
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert "company_id" not in msg
    assert "assigned_user_id" not in msg
    assert "DATE(" not in msg
    assert "LOWER(" not in msg
    assert "LIKE" not in msg
    assert "Vinothini V" in msg


def test_response_node_self_today_uses_neutral_message_for_invalid_name(monkeypatch):
    monkeypatch.setenv("DOMAIN", "maintenance")
    DomainRegistry._instance = None

    node = ResponseNode()
    state = {
        "sql_query": (
            "SELECT tt.id FROM task_transaction tt "
            "JOIN facility f ON tt.facility_id=f.id "
            "WHERE f.company_id='56942516' "
            "AND tt.assigned_user_id=11784212 "
            "AND DATE(tt.scheduled_date)=CURDATE()"
        ),
        "row_count": 0,
        "rows_preview": [],
        "metadata": {
            "user_id": "11784212",
            "user_name": "Kritilabs",
            "company_name": "Kritilabs",
        },
    }
    result = asyncio.run(node.run(state))
    msg = str(result["messages"][0].content)
    assert msg == "You don't have tasks today."
