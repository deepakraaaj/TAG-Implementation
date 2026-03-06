import asyncio

from app.assistant.engine.flow.flow_engine import FlowEngine


class _FakeRegistry:
    def __init__(self, flow):
        self._flow = dict(flow)

    def get(self, flow_id):
        if flow_id != "prefill_flow":
            raise KeyError(flow_id)
        return dict(self._flow)

    @staticmethod
    def has(flow_id):
        return flow_id == "prefill_flow"


class _ResolverPlugin:
    @staticmethod
    def resolvers():
        def _lookup(_ctx, _state_def, _session_state, _page, search_text):
            q = str(search_text or "").strip().lower()
            if q == "nirmala":
                return [{"label": "Nirmala S", "value": "11784788"}]
            if q in {"developers hub", "developer hu"}:
                return [{"label": "Developers Hub | DEVH01", "value": "361"}]
            return [
                {"label": "Mariyammal M", "value": "11784001"},
                {"label": "Dhanam M", "value": "11784002"},
            ]

        return {"generic.lookup": _lookup}

    @staticmethod
    def actions():
        return {}


class _Noop:
    pass


def test_flow_engine_prefill_search_auto_selects_single_lookup_option():
    flow = {
        "id": "prefill_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_assigned_user"},
            "choose_assigned_user": {
                "type": "menu",
                "prompt": "Choose assigned user",
                "capture": "assigned_user",
                "resolver": "generic.lookup",
                "lookup": {
                    "table": "user",
                    "value_column": "id",
                    "label_columns": ["first_name", "last_name"],
                    "search_columns": ["id", "first_name", "last_name"],
                    "page_size": 10,
                    "order_by": "id DESC",
                },
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
        "active_flow": "prefill_flow",
        "current_state": "",
        "flow_context": {"values": {}, "history": [], "prefill_search": {"assigned_user": "nirmala"}},
    }

    result = asyncio.run(engine.run("prefill_flow", session_state, "", metadata={}))
    values = (session_state.get("flow_context") or {}).get("values") or {}

    assert result.completed is True
    assert values.get("assigned_user") == "11784788"


def test_flow_engine_prefilled_static_menu_value_auto_advances():
    flow = {
        "id": "prefill_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_task_for"},
            "choose_task_for": {
                "type": "menu",
                "prompt": "Task For",
                "capture": "task_for",
                "options": [
                    {"label": "Facility", "value": "facility"},
                    {"label": "Asset", "value": "asset"},
                ],
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
        plugins=[],
    )
    session_state = {
        "active_flow": "prefill_flow",
        "current_state": "",
        "flow_context": {"values": {"task_for": "facility"}, "history": [], "prefill_search": {}},
    }

    result = asyncio.run(engine.run("prefill_flow", session_state, "", metadata={}))
    values = (session_state.get("flow_context") or {}).get("values") or {}

    assert result.completed is True
    assert values.get("task_for") == "facility"


def test_flow_engine_prefill_search_auto_selects_assignee_and_facility_sequence():
    flow = {
        "id": "prefill_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_assigned_user"},
            "choose_assigned_user": {
                "type": "menu",
                "prompt": "Choose assigned user",
                "capture": "assigned_user",
                "resolver": "generic.lookup",
                "lookup": {
                    "table": "user",
                    "value_column": "id",
                    "label_columns": ["first_name", "last_name"],
                    "search_columns": ["id", "first_name", "last_name"],
                    "page_size": 10,
                    "order_by": "id DESC",
                },
                "next": "choose_facility",
            },
            "choose_facility": {
                "type": "menu",
                "prompt": "Choose facility",
                "capture": "facility_id_or_name",
                "resolver": "generic.lookup",
                "lookup": {
                    "table": "facility",
                    "value_column": "id",
                    "label_columns": ["name"],
                    "search_columns": ["id", "name"],
                    "page_size": 10,
                    "order_by": "id DESC",
                },
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
        "active_flow": "prefill_flow",
        "current_state": "",
        "flow_context": {
            "values": {},
            "history": [],
            "prefill_search": {
                "assigned_user": "nirmala",
                "facility_id_or_name": "developers hub",
            },
        },
    }

    result = asyncio.run(engine.run("prefill_flow", session_state, "", metadata={}))
    values = (session_state.get("flow_context") or {}).get("values") or {}

    assert result.completed is True
    assert values.get("assigned_user") == "11784788"
    assert values.get("facility_id_or_name") == "361"


def test_flow_engine_prefill_single_match_can_require_confirmation():
    flow = {
        "id": "prefill_flow",
        "start": "start",
        "states": {
            "start": {"type": "system", "next": "choose_facility"},
            "choose_facility": {
                "type": "menu",
                "prompt": "Choose facility",
                "capture": "facility_id_or_name",
                "confirm_single_match": True,
                "resolver": "generic.lookup",
                "lookup": {
                    "table": "facility",
                    "value_column": "id",
                    "label_columns": ["name"],
                    "search_columns": ["id", "name"],
                    "page_size": 10,
                    "order_by": "id DESC",
                },
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
        "active_flow": "prefill_flow",
        "current_state": "",
        "flow_context": {
            "values": {},
            "history": [],
            "prefill_search": {"facility_id_or_name": "developer hu"},
        },
    }

    first = asyncio.run(engine.run("prefill_flow", session_state, "", metadata={}))
    assert first.completed is False
    assert "Is this the one you asked for?" in str(first.message)

    second = asyncio.run(engine.run("prefill_flow", session_state, "yes", metadata={}))
    values = (session_state.get("flow_context") or {}).get("values") or {}
    assert second.completed is True
    assert values.get("facility_id_or_name") == "361"
