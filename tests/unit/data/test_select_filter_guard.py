import asyncio

from app.assistant.nodes.sql_builder_node import SQLBuilderNode
from app.services.data.sql_validator import SQLValidatorService


def test_sql_validator_rejects_select_without_where():
    validator = SQLValidatorService()
    assert validator.validate_sql("SELECT id, status FROM task_transaction LIMIT 100;") is False


def test_sql_validator_accepts_select_with_where():
    validator = SQLValidatorService()
    assert validator.validate_sql("SELECT id, status FROM task_transaction WHERE status = 1 LIMIT 100;") is True


class _FakeCatalog:
    @staticmethod
    def important_columns(_table):
        return {"status", "scheduled_date", "priority", "company_id"}


class _FakeBuilder:
    def __init__(self, sql: str):
        self.catalog = _FakeCatalog()
        self._sql = sql

    @staticmethod
    def resolve_table(_query, _intent):
        return "task_transaction"

    @staticmethod
    def parse_kv_pairs(_query):
        return {}

    async def build_select(self, _query, _table, _company_id):
        return self._sql

    def build_select_from_filters(self, _table, filters, company_id):
        if not filters:
            return self._sql, ""
        where = ["company_id = " + str(company_id)] if company_id else []
        if str(filters.get("status", "")).lower() == "completed":
            where.append("status = 2")
        if str(filters.get("scheduled_date", "")).lower() == "today":
            where.append("DATE(scheduled_date) = CURDATE()")
        sql = "SELECT id, status FROM task_transaction WHERE " + " AND ".join(where) + " LIMIT 100;"
        return sql, ""


def test_sql_builder_node_blocks_unfiltered_select():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder("SELECT id, status FROM task_transaction LIMIT 100;")
    state = {
        "messages": [type("M", (), {"content": "show all tasks"})()],
        "metadata": {"company_id": "1"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SKIP"
    assert result["error"] is None
    assert result["workflow_payload"]["ui"]["type"] == "menu"
    assert len(result["workflow_payload"]["ui"]["options"]) >= 3


def test_sql_builder_node_auto_injects_company_filter_when_explicit_filter_present():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder("SELECT id, status FROM task_transaction WHERE status = 1 LIMIT 100;")
    state = {
        "messages": [type("M", (), {"content": "show completed tasks status=Completed"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {"status": "Completed"}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"].startswith("SELECT")
    assert "company_id = 56942686" in result["sql_query"] or "company_id=56942686" in result["sql_query"]


def test_sql_builder_node_blocks_company_only_filter():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder("SELECT id, status FROM task_transaction WHERE company_id = 56942686 LIMIT 100;")
    state = {
        "messages": [type("M", (), {"content": "show tasks company_id=56942686"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {"company_id": "56942686"}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SKIP"
    assert result["error"] is None


def test_sql_builder_node_allows_company_plus_business_filter():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder(
        "SELECT id, status FROM task_transaction WHERE company_id = 56942686 AND status = 2 LIMIT 100;"
    )
    state = {
        "messages": [type("M", (), {"content": "show completed tasks status=Completed"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {"status": "Completed"}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"].startswith("SELECT")
    assert "status = 2" in result["sql_query"] or "status=2" in result["sql_query"]


def test_sql_builder_node_blocks_select_when_no_explicit_filters_even_with_where_in_sql():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder("SELECT id FROM task_transaction WHERE status IS NOT NULL LIMIT 100;")
    state = {
        "messages": [type("M", (), {"content": "show tasks"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SKIP"
    assert result["error"] is None
    assert result["pending_select"]["table"] == "task_transaction"
    assert result["workflow_payload"]["ui"]["type"] == "menu"


def test_sql_builder_node_accepts_natural_language_filter_today():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder(
        "SELECT id, status FROM task_transaction WHERE company_id = 56942686 AND scheduled_date = CURDATE() LIMIT 100;"
    )
    state = {
        "messages": [type("M", (), {"content": "show task status today"})()],
        "metadata": {"company_id": "56942686"},
        "intent": {"operation": "select", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"].startswith("SELECT")
