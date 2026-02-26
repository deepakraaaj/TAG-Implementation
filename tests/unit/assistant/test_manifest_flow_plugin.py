import asyncio

from app.assistant.engine.flow_plugins.manifest_flow_plugin import ManifestFlowPlugin


class _FakeSchema:
    pass


class _FakeBuilder:
    def __init__(self):
        self.table = ""
        self.fields = {}
        self.company_id = None

    def build_insert(self, table, fields, company_id, actor_user_id=None):
        self.table = table
        self.fields = dict(fields)
        self.company_id = company_id
        return "INSERT INTO x (...) VALUES (...);", ""


class _FakeExecutor:
    async def run(self, _payload):
        return {"row_count": 1, "rows_preview": []}


def _run(coro):
    return asyncio.run(coro)


def test_generic_create_row_builds_fields_from_mapping():
    builder = _FakeBuilder()
    plugin = ManifestFlowPlugin(_FakeSchema(), builder, _FakeExecutor())
    flow = {
        "target_table": "scheduler_task_details",
        "required_fields": ["sche_details_id", "facility_id_or_name", "assigned_user"],
        "field_map": {
            "sche_details_id": "sche_details_id",
            "facility_id_or_name": "facility_id",
            "assigned_user": "user_id",
        },
        "generated_fields": {"scheduled_ref_no": "auto_ref"},
    }
    session_state = {
        "flow_context": {
            "values": {
                "sche_details_id": "69",
                "facility_id_or_name": "10",
                "assigned_user": "25",
            }
        }
    }

    result = _run(plugin._action_create_row(flow, session_state, {"company_id": 7}))
    assert result["status"] == "ok"
    assert builder.table == "scheduler_task_details"
    assert builder.fields["sche_details_id"] == 69
    assert builder.fields["facility_id"] == 10
    assert builder.fields["user_id"] == 25
    assert str(builder.fields["scheduled_ref_no"]).startswith("AUTO-")


def test_generic_create_row_supports_conditional_required_fields():
    plugin = ManifestFlowPlugin(_FakeSchema(), _FakeBuilder(), _FakeExecutor())
    flow = {
        "target_table": "scheduler_task_details",
        "required_fields": ["task_for"],
        "required_when": [
            {"condition": "context.task_for == 'asset'", "fields": ["asset_id_or_name"]},
        ],
        "field_map": {"task_for": "task_for", "asset_id_or_name": "asset_id"},
    }
    session_state = {"flow_context": {"values": {"task_for": "asset"}}}

    result = _run(plugin._action_create_row(flow, session_state, {}))
    assert result["status"] == "error"
    assert "asset_id_or_name" in result["message"]
