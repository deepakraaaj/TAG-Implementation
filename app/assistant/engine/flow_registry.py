from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from app.domains.registry import DomainRegistry


class FlowRegistry:
    """Loads YAML flow definitions from assistant and active domain flow folders."""

    def __init__(self, flows_dir: str | Path | None = None):
        app_dir = Path(__file__).resolve().parents[2]
        assistant_flows_dir = app_dir / "assistant" / "flows"
        self.flow_dirs: list[Path] = []
        if flows_dir:
            self.flow_dirs = [Path(flows_dir)]
        else:
            self.flow_dirs.append(assistant_flows_dir)
            try:
                domain = DomainRegistry.get_current_domain()
                domain_flows_dir = app_dir / "domains" / domain.name / "flows"
                self.flow_dirs.append(domain_flows_dir)
            except Exception:
                # Domain resolution should not block flow loading from assistant defaults.
                pass
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return

        self._flows = {}
        for flow_dir in self.flow_dirs:
            if not flow_dir.exists():
                continue
            files = sorted(flow_dir.glob("*.yml")) + sorted(flow_dir.glob("*.yaml"))
            for path in files:
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
                # Later directories override earlier ones (domain overrides assistant defaults).
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
