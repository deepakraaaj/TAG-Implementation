from app.services.chat import ChatService


def test_summary_intent_detection_keywords():
    assert ChatService._is_summary_request("give me summary for the above list")
    assert ChatService._is_summary_request("how many tasks are complete for now")
    assert not ChatService._is_summary_request("show task list for today")


def test_summary_spec_uses_domain_config_buckets():
    spec = ChatService._normalize_summary_spec(
        {
            "summary": {
                "entity_label": "tickets",
                "status_column": "ticket_status",
                "status_buckets": [
                    {"key": "done", "label": "Done", "values": ["done", "3"]},
                ],
            }
        }
    )
    assert spec["entity_label"] == "tickets"
    assert spec["status_column"] == "ticket_status"
    assert spec["status_buckets"][0]["key"] == "done"


def test_build_summary_sql_uses_specified_status_column_and_values():
    spec = {
        "entity_label": "tickets",
        "status_column": "ticket_status",
        "status_buckets": [
            {"key": "done", "label": "Done", "values": ["done", "3"]},
        ],
    }
    sql = ChatService._build_summary_sql("SELECT * FROM x", spec)
    assert "CAST(ticket_status AS CHAR)" in sql
    assert "AS done_count" in sql
    assert "'done','3'" in sql
