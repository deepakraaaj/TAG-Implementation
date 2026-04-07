import asyncio

from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine

from app.assistant.nodes.sql.sql_execute_node import SQLExecuteNode


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


def test_sql_execute_node_can_store_successful_query_memory():
    class _Schema:
        @staticmethod
        def get_engine_for_url(_db_url):
            return create_engine("sqlite:///:memory:")

    class _Retriever:
        def __init__(self):
            self.calls = []

        def remember_success(self, *, question, sql, candidate_tables=None):
            self.calls.append(
                {
                    "question": question,
                    "sql": sql,
                    "candidate_tables": list(candidate_tables or []),
                }
            )

    retriever = _Retriever()
    node = SQLExecuteNode(
        schema_service=_Schema(),
        domain_provider=lambda: None,
        semantic_retriever=retriever,
        auto_learn_on_success=True,
    )

    result = asyncio.run(
        node.run(
            {
                "sql_query": "SELECT 1 AS id;",
                "messages": [HumanMessage(content="show work orders")],
                "intent": {"table": "task_transaction", "joins": ["person"]},
                "metadata": {"db_connection_string": "sqlite:///:memory:"},
            }
        )
    )

    assert result["error"] is None
    assert retriever.calls
    assert retriever.calls[0]["question"] == "show work orders"
    assert retriever.calls[0]["candidate_tables"] == ["task_transaction", "person"]
