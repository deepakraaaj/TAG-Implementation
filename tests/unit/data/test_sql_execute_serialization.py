from app.assistant.nodes.sql_execute_node import SQLExecuteNode


def test_serialize_row_maps_task_status_code_to_label():
    row = {"id": 1, "status": 2}
    out = SQLExecuteNode._serialize_row(row)
    assert out["status"] == "Completed"


def test_serialize_row_maps_facility_status_code_to_label():
    row = {"id": 1, "facility_status": 3}
    out = SQLExecuteNode._serialize_row(row)
    assert out["facility_status"] == "Delay In Progress"


def test_extract_and_strip_window_total_count():
    rows = [
        {"_total_count": "10", "asset_id": 1, "name": "A"},
        {"_total_count": "10", "asset_id": 2, "name": "B"},
    ]
    total = SQLExecuteNode._extract_window_total_count(rows)
    cleaned = SQLExecuteNode._strip_window_total_count(rows)

    assert total == 10
    assert "_total_count" not in cleaned[0]
    assert cleaned[0]["asset_id"] == 1
