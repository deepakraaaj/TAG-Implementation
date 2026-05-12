from pathlib import Path

import yaml


def _load_flow(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return dict(data)


def test_create_task_flow_is_menu_first_and_carries_defaults():
    flow = _load_flow("app/domains/fits_dev_march_9/flows/create_task.yaml")
    states = dict(flow.get("states") or {})

    assert states.get("start", {}).get("next") == "choose_task_description"
    assert states.get("choose_task_description", {}).get("capture") == "task_description_id"
    assert states.get("choose_facility", {}).get("capture") == "facility_id"
    assert states.get("enter_scheduled_date", {}).get("capture") == "scheduled_date"
    assert states.get("enter_scheduled_date", {}).get("input_kind") == "date"
    assert states.get("choose_priority", {}).get("capture") == "priority"
    assert states.get("choose_assigned_user", {}).get("optional") is True
    assert states.get("choose_asset", {}).get("optional") is True
    assert states.get("choose_asset", {}).get("prompt") == "Choose asset (optional)"
    assert states.get("enter_remarks", {}).get("optional") is True
    assert flow.get("default_fields", {}).get("status") == 0
    assert flow.get("default_fields", {}).get("is_active") == 1
    assert flow.get("generated_fields", {}).get("task_id") == "auto_ref:TASK_"
