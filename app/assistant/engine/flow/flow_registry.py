from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from app.domains.registry import DomainRegistry


class FlowRegistry:
    """Loads YAML flow definitions from assistant and active domain flow folders."""

    def __init__(self, flows_dir: str | Path | None = None):
        # <repo>/app/assistant/engine/flow/flow_registry.py -> parents[3] == <repo>/app
        app_dir = Path(__file__).resolve().parents[3]
        self.app_dir = app_dir
        self._explicit_flows_dir = Path(flows_dir) if flows_dir else None
        self._flows: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._loaded_domain_name = ""

    def _flow_dirs(self) -> list[Path]:
        if self._explicit_flows_dir is not None:
            return [self._explicit_flows_dir]

        flow_dirs: list[Path] = [self.app_dir / "assistant" / "flows"]
        try:
            domain = DomainRegistry.get_current_domain()
            domain_name = str(getattr(domain, "name", "") or getattr(domain, "domain_name", "") or "").strip()
            if domain_name:
                flow_dirs.append(self.app_dir / "domains" / domain_name / "flows")
        except Exception:
            pass
        return flow_dirs

    def _load(self) -> None:
        active_domain_name = ""
        try:
            domain = DomainRegistry.get_current_domain()
            active_domain_name = str(getattr(domain, "name", "") or getattr(domain, "domain_name", "") or "").strip()
        except Exception:
            active_domain_name = ""

        if self._loaded and self._loaded_domain_name == active_domain_name:
            return

        self._flows = {}
        for flow_dir in self._flow_dirs():
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
        self._loaded_domain_name = active_domain_name

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
