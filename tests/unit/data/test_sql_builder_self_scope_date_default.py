import asyncio
from unittest.mock import patch

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


def _msg(text: str):
    return type("M", (), {"content": text})()


class _FakeCatalog:
    @staticmethod
    def table_names():
        return {"task_transaction"}

    @staticmethod
    def important_columns(_table):
        return {"id", "status", "scheduled_date", "assigned_user_id", "company_id"}

    @staticmethod
    def table_meta(table):
        if table != "task_transaction":
            return {}
        return {
            "important_columns": {
                "id": {},
                "status": {},
                "scheduled_date": {},
                "assigned_user_id": {},
                "company_id": {},
            },
            "tenant_scope": {"column": "company_id", "metadata_key": "company_id"},
        }

    @staticmethod
    def aliases(_table):
        return []

    @staticmethod
    def create_enabled(_table):
        return False

    @staticmethod
    def required_create_fields(_table):
        return []

    @staticmethod
    def get_query_template(_table, _template_type):
        return None


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
    async def build_select(_query, _table, _company_id):
        return "SELECT id FROM task_transaction WHERE company_id = 1 LIMIT 100;"

    @staticmethod
    def build_count_from_filters(_table, _filters, _company_id):
        return "SELECT COUNT(*) AS total_count FROM task_transaction;", ""

    @staticmethod
    def build_select_from_filters(_table, filters, company_id):
        where = []
        if company_id:
            where.append(f"company_id = {company_id}")
        assigned_user_id = str(filters.get("assigned_user_id", "")).strip()
        if assigned_user_id:
            where.append(f"assigned_user_id={assigned_user_id}")
        status = str(filters.get("status", "")).strip().lower()
        if status:
            where.append("status=0" if status == "pending" else f"status='{status}'")
        if str(filters.get("scheduled_date", "")).strip().lower() == "today":
            where.append("DATE(scheduled_date) = CURDATE()")
        if not where:
            where = ["1=1"]
        return "SELECT id FROM task_transaction WHERE " + " AND ".join(where) + " LIMIT 100;", ""


class _FakeIntentDetector:
    @staticmethod
    async def detect_intent(_query, _metadata, context_table=""):
        _ = context_table
        return {"operation": "select", "table": "task_transaction", "filters": []}

    @staticmethod
    async def detect_intent_with_usage(_query, _metadata, context_table=""):
        _ = context_table
        return {"operation": "select", "table": "task_transaction", "filters": []}, {}

    @staticmethod
    def fallback_intent(_query):
        return {"operation": "select", "table": "task_transaction", "filters": []}


_ENTITY_BEHAVIOR_CONFIG = {
    "primary_table": "task_transaction",
    "intent_mode": "heuristic",
    "primary_keywords": ["task", "tasks"],
    "primary_filter_keys": ["scheduled_date", "status", "assigned_user_id", "assignee", "assigned_to"],
    "date_filter_keys": ["scheduled_date"],
    "status_filter_key": "status",
    "user_filter_keys": ["assigned_user_id", "assignee", "assigned_to", "user", "user_id"],
    "user_name_filter_key": "assignee",
    "user_id_filter_key": "assigned_user_id",
    "self_aliases": ["my", "me", "mine", "myself"],
    "all_users_aliases": ["all users", "all assignees", "for everyone", "everyone"],
    "primary_date_range_terms": ["today", "yesterday", "this week", "month", "range", "between"],
}


def _make_node():
    node = SQLBuilderNode()
    node.sql_builder = _FakeBuilder()
    node.intent_detector = _FakeIntentDetector()
    return node


def test_named_assignee_query_does_not_force_today_scope():
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("show pending tasks for Nirmala")],
            "metadata": {"company_id": "56942686", "user_id": "11784578"},
            "intent": {
                "operation": "select",
                "table": "task_transaction",
                "filters": {"assigned_user_id": "11784788", "status": "pending"},
            },
        }
        result = asyncio.run(node.run(state))
        sql = str(result.get("sql_query", ""))
        assert "assigned_user_id=11784788" in sql
        assert "DATE(scheduled_date) = CURDATE()" not in sql


def test_self_tasks_query_keeps_today_default_scope():
    node = _make_node()
    with patch.object(SQLBuilderNode, "_entity_behavior_config", return_value=_ENTITY_BEHAVIOR_CONFIG):
        state = {
            "messages": [_msg("show my pending tasks")],
            "metadata": {"company_id": "56942686", "user_id": "11784578"},
            "intent": {
                "operation": "select",
                "table": "task_transaction",
                "filters": {"status": "pending"},
            },
        }
        result = asyncio.run(node.run(state))
        sql = str(result.get("sql_query", ""))
        assert "assigned_user_id=11784578" in sql
        assert "DATE(scheduled_date) = CURDATE()" in sql
