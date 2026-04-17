from pathlib import Path

import yaml


def _load_flow(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return dict(data)


def _assert_sequence(flow: dict) -> None:
    states = dict(flow.get("states") or {})

    choose_task_for = dict(states.get("choose_task_for") or {})
    assert choose_task_for.get("next") == "choose_facility"

    choose_facility = dict(states.get("choose_facility") or {})
    next_def = dict(choose_facility.get("next") or {})
    assert next_def.get("default") == "choose_assigned_user"

    choose_asset = dict(states.get("choose_asset") or {})
    assert choose_asset.get("next") == "choose_assigned_user"

    choose_assigned_user = dict(states.get("choose_assigned_user") or {})
    assert choose_assigned_user.get("next") == "choose_task"


def test_create_schedule_flow_keeps_facility_step_in_sequence():
    maintenance_flow = _load_flow("domains/maintenance/flows/create_schedule.yaml")
    assistant_flow = _load_flow("app/assistant/flows/create_schedule.yaml")

    _assert_sequence(maintenance_flow)
    _assert_sequence(assistant_flow)
