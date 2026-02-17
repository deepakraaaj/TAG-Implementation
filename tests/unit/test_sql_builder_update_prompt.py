import asyncio

from app.assistant.nodes.sql_builder_node import SQLBuilderNode


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

    @staticmethod
    def mutation_form_payload(table, operation, required_fields):
        return {
            "workflow_id": "mutation_menu",
            "state": f"collect_{operation}_{table}",
            "completed": False,
            "collected_data": {"required_fields": required_fields},
        }


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
    required = result["workflow_payload"]["collected_data"]["required_fields"]
    assert required == ["id", "status"]
