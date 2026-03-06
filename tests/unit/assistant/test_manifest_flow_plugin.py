import asyncio

from app.assistant.engine.flow.plugins.manifest_flow_plugin import ManifestFlowPlugin


class _FakeSchema:
    def get_table_columns(self, tables, db_url=None):
        _ = db_url
        return {
            "facility": {"id", "name", "code", "company_id"},
            "user": {"id", "first_name", "last_name", "company_id"},
            "scheduler_task_details": {"id"},
        }

    def get_engine_for_url(self, db_url=None):
        _ = db_url
        return _FakeLookupEngine()


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


class _FakeLookupResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeLookupConnection:
    def __init__(self):
        self.last_params = {}

    def execute(self, _stmt, params):
        self.last_params = dict(params or {})
        sql = str(_stmt)
        q_values = [str(v).lower() for k, v in self.last_params.items() if str(k).startswith("q")]
        if "`user`" in sql.lower():
            if "%shoban%" in q_values or "%soban%" in q_values:
                return _FakeLookupResult([{"id": 11784003, "first_name": "Soban", "last_name": ""}])
            return _FakeLookupResult([])
        if "%developers hub%" in q_values:
            return _FakeLookupResult([{"id": 361, "name": "Developers Hub", "code": "DEVH01"}])
        if "%develop%" in q_values and "%hub%" in q_values:
            return _FakeLookupResult([{"id": 361, "name": "Developers Hub", "code": "DEVH01"}])
        return _FakeLookupResult([])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class _FakeLookupEngine:
    def connect(self):
        return _FakeLookupConnection()


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


def test_lookup_matches_pluralized_first_token_variant():
    plugin = ManifestFlowPlugin(_FakeSchema(), _FakeBuilder(), _FakeExecutor())
    state_def = {
        "lookup": {
            "table": "facility",
            "value_column": "id",
            "label_columns": ["name", "code"],
            "search_columns": ["id", "name", "code"],
            "page_size": 10,
            "order_by": "id DESC",
        }
    }
    session_state = {"flow_context": {"metadata": {"company_id": 56942686}}}

    options = plugin._resolve_lookup({}, state_def, session_state, page=0, search_text="developer hub")

    assert options == [{"value": "361", "label": "Developers Hub | DEVH01"}]


def test_lookup_matches_facility_phrase_with_ignore_terms_and_aliases():
    plugin = ManifestFlowPlugin(_FakeSchema(), _FakeBuilder(), _FakeExecutor())
    state_def = {
        "lookup": {
            "table": "facility",
            "value_column": "id",
            "label_columns": ["name", "code"],
            "search_columns": ["id", "name", "code"],
            "search_ignore_terms": ["facility", "facilities"],
            "search_token_aliases": {"development": ["developer", "developers"]},
            "page_size": 10,
            "order_by": "id DESC",
        }
    }
    session_state = {"flow_context": {"metadata": {"company_id": 56942686}}}

    options = plugin._resolve_lookup({}, state_def, session_state, page=0, search_text="development hub facility")

    assert options == [{"value": "361", "label": "Developers Hub | DEVH01"}]


def test_lookup_matches_user_name_with_sh_s_spelling_variation():
    plugin = ManifestFlowPlugin(_FakeSchema(), _FakeBuilder(), _FakeExecutor())
    state_def = {
        "lookup": {
            "table": "user",
            "value_column": "id",
            "label_columns": ["first_name", "last_name"],
            "search_columns": ["id", "first_name", "last_name"],
            "page_size": 10,
            "order_by": "first_name ASC",
        }
    }
    session_state = {"flow_context": {"metadata": {"company_id": 56942686}}}

    options = plugin._resolve_lookup({}, state_def, session_state, page=0, search_text="shoban")

    assert options == [{"value": "11784003", "label": "Soban"}]
