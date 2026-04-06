import asyncio

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


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


class _FakeVehicleCatalog:
    @staticmethod
    def table_names():
        return {"vehicle"}

    @staticmethod
    def important_columns(_table):
        return {"id", "vehicle_number", "company_id", "is_active"}

    @staticmethod
    def table_meta(_table):
        return {"important_columns": {"id": {}, "vehicle_number": {}, "company_id": {}, "is_active": {}}}


class _FakeVehicleBuilder:
    def __init__(self):
        self.catalog = _FakeVehicleCatalog()

    @staticmethod
    def resolve_table(_query, _intent):
        return "vehicle"

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    @staticmethod
    def build_count_from_filters(_table, filters, _company_id):
        if str(filters.get("is_active", "")).strip() == "1":
            return "SELECT COUNT(*) AS total_count FROM vehicle WHERE company_id = 1 AND is_active = 1;", ""
        return "SELECT COUNT(*) AS total_count FROM vehicle WHERE company_id = 1;", ""


class _FakeVehicleIntentDetector:
    async def detect_intent(self, _query, _metadata):
        return {"operation": "SELECT", "table": "vehicle", "filters": []}


def test_count_intent_infers_is_active_filter_for_active_vehicle_queries():
    node = SQLBuilderNode()
    node.sql_builder = _FakeVehicleBuilder()
    node.intent_detector = _FakeVehicleIntentDetector()

    state = {
        "messages": [type("M", (), {"content": "How many active vehicles are there"})()],
        "metadata": {"company_id": "1"},
        "intent": {"operation": "select", "table": "vehicle", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))

    assert "is_active = 1" in result["sql_query"]
