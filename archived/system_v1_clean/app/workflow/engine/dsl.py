from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class WorkflowStateDefinition:
    name: str
    type: str
    next_state: Optional[str]
    resolver: Optional[str] = None
    validator: Optional[str] = None
    action: Optional[str] = None
    count_resolver: Optional[str] = None
    capture_key: Optional[str] = None
    template: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    optional: bool = False
    ui: Dict[str, object] = field(default_factory=dict)
    config: Dict[str, object] = field(default_factory=dict)


@dataclass
class WorkflowDefinition:
    workflow_id: str
    title: str
    description: Optional[str]
    start_state: str
    states: Dict[str, WorkflowStateDefinition]

    def get_state(self, name: str) -> WorkflowStateDefinition:
        if name not in self.states:
            raise KeyError(f"State {name} not defined for workflow {self.workflow_id}")
        return self.states[name]


class WorkflowRegistry:
    """Loads workflow definitions from JSON/YAML files."""

    def __init__(self, definitions_path: str | Path) -> None:
        self.definitions_path = Path(definitions_path)
        self.registry: Dict[str, WorkflowDefinition] = {}
        self._load_definitions()

    def _load_definitions(self) -> None:
        if not self.definitions_path.exists():
            return
        for file_path in sorted(self.definitions_path.glob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            payload = self._read_file(file_path)
            definition = self._build_definition(payload)
            self.registry[definition.workflow_id] = definition

    def _read_file(self, path: Path) -> Dict[str, object]:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text) or {}

    def _build_definition(self, payload: Dict[str, object]) -> WorkflowDefinition:
        workflow_id = (
            str(payload.get("id") or payload.get("workflow_id")).strip().upper()
        )
        if not workflow_id:
            raise ValueError("Workflow definition missing id/workflow_id.")
        title = str(payload.get("title") or workflow_id).strip()
        start_state = str(payload.get("start_state") or "start")
        states_payload = payload.get("states") or {}
        states: Dict[str, WorkflowStateDefinition] = {}
        for name, state_payload in states_payload.items():
            states[name] = WorkflowStateDefinition(
                name=name,
                type=str(state_payload.get("type") or "menu"),
                next_state=state_payload.get("next"),
                resolver=state_payload.get("resolver"),
                validator=state_payload.get("validator"),
                action=state_payload.get("action"),
                count_resolver=state_payload.get("count_resolver"),
                capture_key=state_payload.get("capture"),
                template=list(state_payload.get("template") or []),
                options=list(state_payload.get("options") or []),
                optional=bool(state_payload.get("optional") or False),
                ui=state_payload.get("ui") or {},
                config=state_payload.get("config") or {},
            )
        return WorkflowDefinition(
            workflow_id=workflow_id,
            title=title,
            description=payload.get("description"),
            start_state=start_state,
            states=states,
        )

    def has_workflow(self, workflow_id: str) -> bool:
        return workflow_id.upper() in self.registry

    def get(self, workflow_id: str) -> WorkflowDefinition:
        key = workflow_id.upper()
        if key not in self.registry:
            raise KeyError(f"Workflow {workflow_id} not registered.")
        return self.registry[key]
