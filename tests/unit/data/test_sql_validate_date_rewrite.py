import asyncio

from app.assistant.nodes.sql.sql_validate_node import SQLValidateNode


class _StubSchema:
    @staticmethod
    def get_table_columns(_tables, db_url=None):
        return {"task_transaction": {"scheduled_date", "assigned_user_id"}}

    @staticmethod
    def get_table_column_types(_tables, db_url=None):
        return {"task_transaction": {"scheduled_date": "DATETIME"}}


class _StubValidator:
    def __init__(self):
        self.last_sql = None

    @staticmethod
    def get_tables(_sql):
        return ["task_transaction"]

    def validate_sql(self, sql, table_columns=None):
        self.last_sql = sql
        return True


def test_sql_validate_node_rewrites_date_equality_for_datetime_column():
    node = SQLValidateNode()
    node.schema = _StubSchema()
    validator = _StubValidator()
    node.validator = validator

    state = {
        "sql_query": (
            "SELECT id FROM task_transaction "
            "WHERE assigned_user_id=11784788 AND scheduled_date='2026-02-18' LIMIT 100;"
        ),
        "metadata": {},
    }

    result = asyncio.run(node.run(state))
    assert result["error"] is None
    rewritten = result["sql_query"]
    assert "scheduled_date >= '2026-02-18 00:00:00'" in rewritten
    assert "scheduled_date < '2026-02-19 00:00:00'" in rewritten
    assert validator.last_sql == rewritten


def test_sql_validate_node_does_not_rewrite_non_date_literal():
    node = SQLValidateNode()
    node.schema = _StubSchema()
    validator = _StubValidator()
    node.validator = validator

    original_sql = (
        "SELECT id FROM task_transaction "
        "WHERE assigned_user_id=11784788 AND scheduled_date='2026-02-18 12:15:00' LIMIT 100;"
    )
    state = {"sql_query": original_sql, "metadata": {}}
    result = asyncio.run(node.run(state))
    assert result["error"] is None
    assert result["sql_query"] == validator.last_sql
    assert "scheduled_date='2026-02-18 12:15:00'" in result["sql_query"]


def test_sql_validate_node_preserves_date_sub_expression_when_no_rewrite_needed():
    node = SQLValidateNode()
    node.schema = _StubSchema()
    validator = _StubValidator()
    node.validator = validator

    original_sql = (
        "SELECT id FROM task_transaction "
        "WHERE DATE(scheduled_date) = DATE_SUB(CURDATE(), INTERVAL 1 DAY) LIMIT 100;"
    )
    state = {"sql_query": original_sql, "metadata": {}}

    result = asyncio.run(node.run(state))
    assert result["error"] is None
    assert result["sql_query"] == original_sql
    assert "INTERVAL 1 DAY" in result["sql_query"]
