from app.assistant.nodes.sql_execute_node import SQLExecuteNode


def test_serialize_row_maps_task_status_code_to_label():
    row = {"id": 1, "status": 2}
    out = SQLExecuteNode._serialize_row(row)
    assert out["status"] == "Completed"


def test_serialize_row_maps_facility_status_code_to_label():
    row = {"id": 1, "facility_status": 3}
    out = SQLExecuteNode._serialize_row(row)
    assert out["facility_status"] == "Delay In Progress"
