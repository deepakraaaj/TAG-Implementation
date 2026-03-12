import asyncio

from app.assistant.nodes.sql.sql_validate_node import SQLValidateNode
from app.services.observability.metrics_service import MetricsService


def test_mutation_policy_override_parses_truthy_values():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({"allow_mutations": True}) is True
    assert node._parse_allow_mutations_flag({"allow_mutations": "true"}) is True
    assert node._parse_allow_mutations_flag({"allow_mutations": "yes"}) is True


def test_mutation_policy_override_parses_falsy_values():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({"allow_mutations": False}) is False
    assert node._parse_allow_mutations_flag({"allow_mutations": "false"}) is False
    assert node._parse_allow_mutations_flag({"allow_mutations": "0"}) is False


def test_mutation_policy_override_returns_none_when_not_set():
    node = SQLValidateNode()
    assert node._parse_allow_mutations_flag({}) is None


def test_mutation_requires_explicit_permission_and_allowed_role():
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"admin"}
    node.require_explicit_mutation_permission = True

    assert node._mutation_policy_override({"allow_mutations": True, "user_role": "admin"}, is_mutation=True) is True
    assert node._mutation_policy_override({"allow_mutations": True, "user_role": "user"}, is_mutation=True) is False
    assert node._mutation_policy_override({"user_role": "admin"}, is_mutation=True) is False


def test_mutation_policy_accepts_role_from_role_key():
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"manager"}
    node.require_explicit_mutation_permission = True

    assert node._mutation_policy_override({"allow_mutations": "true", "role": "manager"}, is_mutation=True) is True


def test_mutation_denied_records_metric(monkeypatch):
    node = SQLValidateNode()
    node.allowed_mutation_roles = {"admin"}
    node.require_explicit_mutation_permission = True

    calls = {"count": 0}

    def _record_denied(reason: str = "policy"):
        if reason == "role_or_policy":
            calls["count"] += 1

    monkeypatch.setattr(MetricsService, "record_mutation_denied", staticmethod(_record_denied))

    result = asyncio.run(
        node.run(
            {
                "sql_query": "UPDATE task_transaction SET status=2 WHERE id=1;",
                "metadata": {"allow_mutations": "true", "user_role": "user"},
            }
        )
    )

    assert result["error"] == "Mutation not allowed for current role/policy."
    assert calls["count"] == 1


def test_task_status_update_scope_allows_single_row_user_update():
    schema_stub = type(
        "_SchemaStub",
        (),
        {
            "get_table_columns": staticmethod(lambda _tables, db_url=None: {}),
            "get_table_column_types": staticmethod(lambda _tables, db_url=None: {}),
        },
    )()
    node = SQLValidateNode(schema_service=schema_stub)
    node.allowed_mutation_roles = {"admin"}
    node.require_explicit_mutation_permission = True

    result = asyncio.run(
        node.run(
            {
                "sql_query": "UPDATE task_transaction SET status=2 WHERE id=1 AND company_id=56942686;",
                "metadata": {
                    "allow_mutations": True,
                    "mutation_scope": "task_status_update",
                    "user_role": "user",
                    "company_id": 56942686,
                },
            }
        )
    )

    assert result["error"] is None


def test_task_status_update_scope_rejects_non_status_changes():
    schema_stub = type(
        "_SchemaStub",
        (),
        {
            "get_table_columns": staticmethod(lambda _tables, db_url=None: {}),
            "get_table_column_types": staticmethod(lambda _tables, db_url=None: {}),
        },
    )()
    node = SQLValidateNode(schema_service=schema_stub)
    node.allowed_mutation_roles = {"admin"}
    node.require_explicit_mutation_permission = True

    result = asyncio.run(
        node.run(
            {
                "sql_query": "UPDATE task_transaction SET priority=1 WHERE id=1 AND company_id=56942686;",
                "metadata": {
                    "allow_mutations": True,
                    "mutation_scope": "task_status_update",
                    "user_role": "user",
                    "company_id": 56942686,
                },
            }
        )
    )

    assert result["error"] == "Mutation not allowed for current role/policy."
