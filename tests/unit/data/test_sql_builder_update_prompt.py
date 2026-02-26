import asyncio

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


class _FakeCatalog:
    @staticmethod
    def important_columns(_table):
        return {"id", "status", "company_id"}

    @staticmethod
    def create_enabled(_table):
        return True

    @staticmethod
    def required_create_fields(_table):
        return []


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
    def build_update(_table, _fields, _company_id, actor_user_id=None):
        return "", "Update requires id=<record_id>."

def test_update_task_status_prompts_for_id_and_status():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder()
    state = {
        "messages": [type("M", (), {"content": "Update Task Status"})()],
        "metadata": {"company_id": "56942686", "user_id": "11784578"},
        "intent": {"operation": "update", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SKIP"
    assert "Update requires id=<record_id>" in result["messages"][-1].content
    assert "workflow_payload" not in result
