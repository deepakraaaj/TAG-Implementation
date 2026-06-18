"""Runtime behaviour tests for the REMP guided flows.

These drive the *real* REMP flow definitions (app/domains/REMP/flows/*.yaml)
through the real FlowEngine and the real ManifestFlowPlugin action logic
(field_map / default_fields / generated_fields / required_when). Only the
boundaries are faked:

* ``generic.lookup`` resolver -> canned options (no DB),
* the SQL builder -> captures the (table, fields) it would build,
* the SQL executor -> returns a configurable ok/error result.

So a test asserts on exactly what would have been written to the DB without
touching MySQL, covering: menu capture, input validation, optional skips,
conditional branching, confirmation, defaults/generated fields, the closed_*
side effects on completion, db-write error handling, and cancel.
"""

import asyncio
from pathlib import Path

import yaml

from app.assistant.engine.flow.flow_engine import FlowEngine

FLOW_DIR = Path("app/domains/REMP/flows")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Registry:
    def __init__(self, flow: dict):
        self._flow = dict(flow)
        self.fid = str(flow.get("id"))

    def get(self, flow_id):
        if flow_id != self.fid:
            raise KeyError(flow_id)
        return dict(self._flow)

    def has(self, flow_id):
        return flow_id == self.fid


class _ResolverPlugin:
    """Overrides generic.lookup with two canned options per menu (values 101/102)."""

    @staticmethod
    def resolvers():
        def _lookup(_ctx, state_def, _session_state, _page, _search_text):
            cap = str(state_def.get("capture", "")).strip() or "item"
            return [
                {"label": f"{cap} one", "value": "101"},
                {"label": f"{cap} two", "value": "102"},
            ]

        return {"generic.lookup": _lookup}

    @staticmethod
    def actions():
        return {}


class _FakeBuilder:
    """Captures what would be inserted/updated; returns a placeholder SQL string."""

    def __init__(self):
        self.insert_calls = []
        self.update_calls = []

    def build_insert(self, table, fields, company_id, actor_user_id=None):
        self.insert_calls.append({"table": table, "fields": dict(fields), "company_id": company_id})
        return (f"INSERT INTO `{table}` (...) VALUES (...)", "")

    def build_update(self, table, fields, company_id, actor_user_id=None):
        self.update_calls.append({"table": table, "fields": dict(fields), "company_id": company_id})
        return (f"UPDATE `{table}` SET ... WHERE id=...", "")

    @staticmethod
    def _normalize_enum_value(_column, value):
        return value


class _FakeExecutor:
    def __init__(self, error=None, row_count=1):
        self.error = error
        self.row_count = row_count
        self.calls = []

    async def run(self, state):
        self.calls.append(dict(state))
        return {"error": self.error, "row_count": self.row_count}


class _FakeCatalog:
    @staticmethod
    def important_columns(_table):
        return {
            "id", "status", "remarks", "date_updated", "closed_time", "closed_by",
            "assigned_user_id", "task_id",
        }


class _Noop:
    pass


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _load_flow(name: str) -> dict:
    return yaml.safe_load((FLOW_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _make_engine(flow: dict, *, executor_error=None):
    from app.assistant.engine.flow.plugins.manifest_flow_plugin import ManifestFlowPlugin

    builder = _FakeBuilder()
    executor = _FakeExecutor(error=executor_error)
    real_plugin = ManifestFlowPlugin(_Noop(), builder, executor, manifest_catalog=_FakeCatalog())
    engine = FlowEngine(
        registry=_Registry(flow),
        schema_service=_Noop(),
        sql_builder_service=builder,
        sql_executor=executor,
        # resolver plugin LAST so its generic.lookup overrides the real DB one.
        plugins=[real_plugin, _ResolverPlugin()],
    )
    return engine, builder, executor


DEFAULT_META = {"company_id": 56942686, "user_id": 999, "db_connection_string": "mysql://x/REMPRecent"}


def drive(engine, fid, inputs, metadata=DEFAULT_META):
    """Run an initial render then each input within one event loop; return (result, session)."""

    async def _seq():
        session = {"active_flow": fid, "current_state": "", "flow_context": {"values": {}, "history": []}}
        result = await engine.run(fid, session, "", metadata)
        for text in inputs:
            result = await engine.run(fid, session, text, metadata)
        return result, session

    return asyncio.run(_seq())


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

def test_create_task_happy_path_builds_insert_with_defaults_and_generated():
    engine, builder, _ = _make_engine(_load_flow("create_task"))
    # task_desc -> facility -> date -> priority(High=2, selected by label) -> skip x3 -> confirm
    result, _ = drive(engine, "create_task",
                      ["101", "101", "2026-12-31", "High", "skip", "skip", "skip", "yes"])
    assert result.completed is True
    assert "successful" in result.message.lower()
    assert len(builder.insert_calls) == 1
    call = builder.insert_calls[0]
    assert call["table"] == "task_transaction"
    f = call["fields"]
    assert str(f["task_description_id"]) == "101"
    assert str(f["facility_id"]) == "101"
    assert f["scheduled_date"] == "2026-12-31"
    assert str(f["priority"]) == "2"
    # defaults applied
    assert str(f["status"]) == "0"
    assert str(f["is_active"]) == "1"
    assert "date_created" in f and "date_updated" in f
    # generated task_id present and prefixed
    assert "task_id" in f and str(f["task_id"]).startswith("TASK_")
    # skipped optionals absent
    assert "assigned_user_id" not in f
    assert "asset_id" not in f
    assert "remarks" not in f


def test_create_task_optional_fields_are_captured_when_provided():
    engine, builder, _ = _make_engine(_load_flow("create_task"))
    result, _ = drive(engine, "create_task",
                      ["101", "101", "2026-12-31", "1", "102", "102", "do it carefully", "yes"])
    assert result.completed is True
    f = builder.insert_calls[0]["fields"]
    assert str(f["assigned_user_id"]) == "102"
    assert str(f["asset_id"]) == "102"
    assert f["remarks"] == "do it carefully"


def test_create_task_invalid_priority_is_rejected():
    engine, builder, _ = _make_engine(_load_flow("create_task"))
    result, session = drive(engine, "create_task", ["101", "101", "2026-12-31", "9"])
    assert result.status == "error"
    assert "invalid selection" in result.message.lower()
    assert session["current_state"] == "choose_priority"  # did not advance
    assert builder.insert_calls == []


def test_create_task_cancel_aborts_without_write():
    engine, builder, _ = _make_engine(_load_flow("create_task"))
    result, _ = drive(engine, "create_task", ["101", "cancel"])
    assert result.completed is True
    assert result.clear_state is True
    assert "cancel" in result.message.lower()
    assert builder.insert_calls == []


# ---------------------------------------------------------------------------
# create_schedule  (conditional branching + numeric validator)
# ---------------------------------------------------------------------------

def test_create_schedule_facility_branch_skips_asset():
    engine, builder, _ = _make_engine(_load_flow("create_schedule"))
    # scheduler -> task_for=facility -> facility -> assigned_user -> task -> priority -> est_time
    result, session = drive(engine, "create_schedule",
                            ["101", "facility", "101", "101", "101", "1", "30", "yes"])
    assert result.completed is True, session.get("current_state")
    call = builder.insert_calls[0]
    assert call["table"] == "scheduler_task_details"
    f = call["fields"]
    assert "asset_id" not in f  # asset branch skipped for facility
    assert str(f["task_est_time"]) == "30"
    assert "scheduled_ref_no" in f  # generated auto_ref


def test_create_schedule_asset_branch_captures_asset():
    engine, builder, _ = _make_engine(_load_flow("create_schedule"))
    # task_for=asset -> facility -> asset -> assigned_user -> task -> priority -> est_time
    result, session = drive(engine, "create_schedule",
                            ["101", "asset", "101", "102", "101", "101", "2", "45", "yes"])
    assert result.completed is True, session.get("current_state")
    f = builder.insert_calls[0]["fields"]
    assert str(f["asset_id"]) == "102"  # asset branch captured + mapped


def test_create_schedule_est_time_rejects_non_numeric():
    engine, builder, _ = _make_engine(_load_flow("create_schedule"))
    result, session = drive(engine, "create_schedule",
                            ["101", "facility", "101", "101", "101", "1", "abc"])
    assert result.status == "error"
    assert session["current_state"] == "enter_est_time"
    assert builder.insert_calls == []


# ---------------------------------------------------------------------------
# assign_task / update_checklist / update_task_status  (db_write = update)
# ---------------------------------------------------------------------------

def test_assign_task_happy_path_builds_update():
    engine, builder, _ = _make_engine(_load_flow("assign_task"))
    result, _ = drive(engine, "assign_task", ["101", "102", "skip", "yes"])
    assert result.completed is True
    assert len(builder.update_calls) == 1
    call = builder.update_calls[0]
    assert call["table"] == "task_transaction"
    assert str(call["fields"]["id"]) == "101"
    assert str(call["fields"]["assigned_user_id"]) == "102"
    assert "date_updated" in call["fields"]  # default applied


def test_update_checklist_happy_path_builds_update():
    engine, builder, _ = _make_engine(_load_flow("update_checklist"))
    result, _ = drive(engine, "update_checklist", ["101", "In Progress", "skip", "yes"])
    assert result.completed is True
    call = builder.update_calls[0]
    assert call["table"] == "check_list_transaction"
    assert str(call["fields"]["id"]) == "101"
    assert str(call["fields"]["status"]) == "1"


def test_update_task_status_completed_sets_closed_fields():
    engine, builder, _ = _make_engine(_load_flow("update_task_status"))
    result, _ = drive(engine, "update_task_status", ["101", "Completed", "skip", "yes"])
    assert result.completed is True
    f = builder.update_calls[0]["fields"]
    assert str(f["status"]) == "2"
    assert "closed_time" in f  # status==2 (Completed) side effects
    assert str(f["closed_by"]) == "999"


def test_update_task_status_non_completed_has_no_closed_fields():
    engine, builder, _ = _make_engine(_load_flow("update_task_status"))
    result, _ = drive(engine, "update_task_status", ["101", "1", "skip", "yes"])
    assert result.completed is True
    f = builder.update_calls[0]["fields"]
    assert "closed_time" not in f
    assert "closed_by" not in f


def test_db_write_error_returns_to_confirm_without_completing():
    engine, builder, executor = _make_engine(_load_flow("update_task_status"), executor_error="Deadlock found")
    result, session = drive(engine, "update_task_status", ["101", "2", "skip", "yes"])
    assert result.status == "error"
    assert result.completed is not True
    assert "Deadlock" in result.message
    assert session["current_state"] == "confirm"  # on_error returns to confirm
