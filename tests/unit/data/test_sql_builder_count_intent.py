import asyncio

from app.assistant.nodes.sql_builder_node import SQLBuilderNode


class _FakeCatalog:
    @staticmethod
    def table_names():
        return {"task_transaction"}

    @staticmethod
    def important_columns(_table):
        return {"scheduled_date", "status", "company_id", "assigned_user_id"}

    @staticmethod
    def table_meta(_table):
        return {"important_columns": {"scheduled_date": {}, "status": {}, "company_id": {}, "assigned_user_id": {}}}


class _FakeBuilder:
    def __init__(self):
        self.catalog = _FakeCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return "task_transaction"

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    @staticmethod
    def build_count_from_filters(_table, filters, _company_id):
        if str(filters.get("scheduled_date", "")).lower() == "today":
            return "SELECT COUNT(*) AS total_tasks FROM task_transaction WHERE DATE(scheduled_date)=CURDATE();", ""
        return "SELECT COUNT(*) AS total_tasks FROM task_transaction;", ""


class _FakeIntentDetector:
    async def detect_intent(self, _query, _metadata):
        return {"operation": "SELECT", "table": "task_transaction", "filters": []}


def test_count_intent_executes_count_sql_without_filter_menu():
    node = SQLBuilderNode()
    node.sql_builder = _FakeBuilder()
    node.intent_detector = _FakeIntentDetector()

    state = {
        "messages": [type("M", (), {"content": "count of today's tasks"})()],
        "metadata": {"company_id": "1"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"].startswith("SELECT COUNT(*)")
    assert "workflow_payload" not in result
    assert result.get("pending_select") is None
