from pathlib import Path

import yaml

from app.assistant.engine.flow.flow_registry import FlowRegistry


def _load_flow(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle) or {})


def test_fits_runtime_flow_registry_sees_all_crud_flows():
    registry = FlowRegistry(flows_dir="app/domains/REMP/flows")

    assert registry.has("create_task")
    assert registry.has("assign_task")
    assert registry.has("update_task_status")
    assert registry.has("update_checklist")
    assert registry.has("create_schedule")


def test_fits_runtime_crud_flows_are_menu_driven():
    flows_dir = Path("app/domains/REMP/flows")
    assign_task = _load_flow(flows_dir / "assign_task.yaml")
    update_status = _load_flow(flows_dir / "update_task_status.yaml")
    update_checklist = _load_flow(flows_dir / "update_checklist.yaml")
    create_schedule = _load_flow(flows_dir / "create_schedule.yaml")

    assert assign_task.get("states", {}).get("write_to_db", {}).get("action") == "generic.update_row"
    assert update_status.get("states", {}).get("choose_status", {}).get("type") == "menu"
    assert update_status.get("states", {}).get("write_to_db", {}).get("action") == "generic.update_row"
    assert update_checklist.get("states", {}).get("choose_checklist_record", {}).get("type") == "menu"
    assert update_checklist.get("states", {}).get("write_to_db", {}).get("action") == "generic.update_row"
    assert create_schedule.get("states", {}).get("write_to_db", {}).get("action") == "generic.create_row"
