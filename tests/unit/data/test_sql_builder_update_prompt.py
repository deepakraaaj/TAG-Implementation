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


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConnection(self._rows)


class _FakeSchema:
    def __init__(self, rows):
        self._rows = rows

    def get_engine_for_url(self, _db_url):
        return _FakeEngine(self._rows)


def test_update_task_status_prompts_for_task_and_status_without_exposing_id():
    node = SQLBuilderNode()
    node.builder = _FakeBuilder()
    state = {
        "messages": [type("M", (), {"content": "Update Task Status"})()],
        "metadata": {"company_id": "56942686", "user_id": "11784578"},
        "intent": {"operation": "update", "table": "task_transaction", "fields": {}, "filters": {}},
    }

    result = asyncio.run(node.run(state))
    assert result["sql_query"] == "SKIP"
    assert "what should change" in result["messages"][-1].content.lower()
    assert "id=<record_id>" not in result["messages"][-1].content
    assert "mark the task as completed" in result["messages"][-1].content.lower()
    assert "workflow_payload" not in result


def test_update_task_status_with_target_status_returns_task_picker():
    node = SQLBuilderNode(schema=_FakeSchema([
        {
            "id": 321,
            "task_id": "TT-44",
            "task_name": "Conference room cleaning",
            "status": "Pending",
            "scheduled_date": "2026-03-10 09:00:00",
            "facility_name": "Main Building",
            "assignee_name": "Mariyammal",
        }
    ]))
    node.builder = _FakeBuilder()
    state = {
        "messages": [type("M", (), {"content": "Update task status to Completed"})()],
        "metadata": {
            "company_id": "56942686",
            "user_id": "11784578",
            "db_connection_string": "mysql://example",
        },
        "intent": {
            "operation": "update",
            "table": "task_transaction",
            "fields": {"status": "Completed"},
            "filters": {},
        },
    }

    result = asyncio.run(node.run(state))

    assert result["sql_query"] == "SKIP"
    assert "which task to update" in result["messages"][-1].content.lower()
    assert "id=" not in result["messages"][-1].content.lower()
    assert result["pending_select"]["mode"] == "update_selection"
    assert result["pending_select"]["update_fields"]["status"] == "Completed"
    assert result["workflow_payload"]["ui"]["options"][0]["label"].startswith("Conference room cleaning")
    assert result["workflow_payload"]["ui"]["options"][0]["value"] == "1"
