from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


class FlowRegistry:
    """Loads YAML flow definitions from app/assistant/flows."""

    def __init__(self, flows_dir: str | Path | None = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.flows_dir = Path(flows_dir) if flows_dir else (base_dir / "flows")
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return

        self._flows = {}
        if not self.flows_dir.exists():
            self._loaded = True
            return

        for path in sorted(self.flows_dir.glob("*.yml")) + sorted(self.flows_dir.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}

            if not isinstance(data, dict):
                continue

            flow_id = str(data.get("id") or path.stem).strip()
            if not flow_id:
                continue

            data.setdefault("id", flow_id)
            data.setdefault("start", "start")
            data.setdefault("states", {})
            self._flows[flow_id] = data

        self._loaded = True

    def get(self, flow_id: str) -> Dict[str, Any]:
        self._load()
        flow = self._flows.get(str(flow_id).strip())
        if not flow:
            raise KeyError(f"Unknown flow: {flow_id}")
        return dict(flow)

    def has(self, flow_id: str) -> bool:
        self._load()
        return str(flow_id).strip() in self._flows

    def all_flow_ids(self) -> list[str]:
        self._load()
        return sorted(self._flows.keys())
