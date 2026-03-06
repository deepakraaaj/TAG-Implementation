import asyncio

from app.assistant.engine.flow.flow_engine import FlowEngine


class _FakeRegistry:
    def __init__(self, flow):
        self._flow = dict(flow)

    def get(self, flow_id):
        if flow_id != "display_flow":
            raise KeyError(flow_id)
        return dict(self._flow)

    @staticmethod
    def has(flow_id):
        return flow_id == "display_flow"


class _ResolverPlugin:
    @staticmethod
    def resolvers():
        def _lookup(_ctx, state_def, _session_state, _page, _search_text):
            capture = str(state_def.get("capture", "")).strip()
            if capture == "sche_details_id":
                return [{"label": "5.15 pm", "value": "81"}]
            if capture == "assigned_user":
                return [{"label": "Mariyammal | M", "value": "11784577"}]
            return []

        return {"generic.lookup": _lookup}

    @staticmethod
    def actions():
        return {}


class _Noop:
    pass


def test_confirmation_shows_menu_labels_not_internal_ids():
    flow = {
        "id": "display_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_scheduler"},
            "choose_scheduler": {
                "type": "menu",
                "prompt": "Choose scheduler reference",
                "capture": "sche_details_id",
                "resolver": "generic.lookup",
                "lookup": {"table": "scheduler_details", "value_column": "id", "label_columns": ["name"]},
                "next": "choose_task_for",
            },
            "choose_task_for": {
                "type": "menu",
                "prompt": "Task For",
                "capture": "task_for",
                "options": [
                    {"label": "Facility", "value": "facility"},
                    {"label": "Asset", "value": "asset"},
                ],
                "next": "choose_assigned_user",
            },
            "choose_assigned_user": {
                "type": "menu",
                "prompt": "Choose assigned user",
                "capture": "assigned_user",
                "resolver": "generic.lookup",
                "lookup": {"table": "user", "value_column": "id", "label_columns": ["first_name", "last_name"]},
                "next": "choose_priority",
            },
            "choose_priority": {
                "type": "menu",
                "prompt": "Choose priority",
                "capture": "priority",
                "options": [
                    {"label": "High", "value": "1"},
                    {"label": "Medium", "value": "2"},
                    {"label": "Low", "value": "3"},
                ],
                "next": "confirm",
            },
            "confirm": {"type": "confirmation", "prompt": "Review and confirm CREATE_SCHEDULE request", "next": "done"},
            "done": {"type": "end", "message": "done"},
        },
    }

    engine = FlowEngine(
        registry=_FakeRegistry(flow),
        schema_service=_Noop(),
        sql_builder_service=_Noop(),
        sql_executor=_Noop(),
        plugins=[_ResolverPlugin()],
    )
    session_state = {
        "active_flow": "display_flow",
        "current_state": "",
        "flow_context": {"values": {}, "history": []},
    }

    asyncio.run(engine.run("display_flow", session_state, "", metadata={}))
    asyncio.run(engine.run("display_flow", session_state, "81", metadata={}))
    asyncio.run(engine.run("display_flow", session_state, "facility", metadata={}))
    asyncio.run(engine.run("display_flow", session_state, "11784577", metadata={}))
    result = asyncio.run(engine.run("display_flow", session_state, "2", metadata={}))

    message = str(result.message)
    assert "- Scheduler Reference: 5.15 pm" in message
    assert "- Task For: Facility" in message
    assert "- Assigned User: Mariyammal | M" in message
    assert "- Priority: Medium" in message
    assert "- Scheduler Reference: 81" not in message
    assert "- Assigned User: 11784577" not in message
    assert "- Priority: 2" not in message


def test_menu_workflow_payload_uses_safe_option_values():
    flow = {
        "id": "display_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_assigned_user"},
            "choose_assigned_user": {
                "type": "menu",
                "prompt": "Choose assigned user",
                "capture": "assigned_user",
                "resolver": "generic.lookup",
                "lookup": {"table": "user", "value_column": "id", "label_columns": ["first_name", "last_name"]},
                "next": "done",
            },
            "done": {"type": "end", "message": "done"},
        },
    }

    engine = FlowEngine(
        registry=_FakeRegistry(flow),
        schema_service=_Noop(),
        sql_builder_service=_Noop(),
        sql_executor=_Noop(),
        plugins=[_ResolverPlugin()],
    )
    session_state = {
        "active_flow": "display_flow",
        "current_state": "",
        "flow_context": {"values": {}, "history": []},
    }

    result = asyncio.run(engine.run("display_flow", session_state, "", metadata={}))
    workflow = dict(result.workflow or {})
    options = ((workflow.get("ui") or {}).get("options") or [])

    assert options
    assert options[0]["label"] == "Mariyammal | M"
    assert options[0]["value"] == "Mariyammal | M"
    assert options[0]["choice"] == "1"


def test_workflow_payload_exposes_display_fields_for_collected_menu_values():
    flow = {
        "id": "display_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_priority"},
            "choose_priority": {
                "type": "menu",
                "prompt": "Choose priority",
                "capture": "priority",
                "options": [
                    {"label": "High", "value": "1"},
                    {"label": "Medium", "value": "2"},
                    {"label": "Low", "value": "3"},
                ],
                "next": "confirm",
            },
            "confirm": {"type": "confirmation", "prompt": "Confirm", "next": "done"},
            "done": {"type": "end", "message": "done"},
        },
    }

    engine = FlowEngine(
        registry=_FakeRegistry(flow),
        schema_service=_Noop(),
        sql_builder_service=_Noop(),
        sql_executor=_Noop(),
        plugins=[],
    )
    session_state = {
        "active_flow": "display_flow",
        "current_state": "",
        "flow_context": {"values": {}, "history": []},
    }

    asyncio.run(engine.run("display_flow", session_state, "", metadata={}))
    result = asyncio.run(engine.run("display_flow", session_state, "Medium", metadata={}))

    collected = dict((result.workflow or {}).get("collected_data") or {})
    assert collected.get("collected_fields", {}).get("priority") == "2"
    assert collected.get("display_fields", {}).get("priority") == "Medium"
