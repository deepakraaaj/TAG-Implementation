from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import logging

from app.services.cache import cache

logger = logging.getLogger(__name__)

@dataclass
class WorkflowSessionSnapshot:
    session_id: str
    workflow_id: str
    current_state: Optional[str] = None
    collected_data: Dict[str, Any] = field(default_factory=dict)
    menu_cache: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "current_state": self.current_state,
            "collected_data": self.collected_data,
            "menu_cache": self.menu_cache,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkflowSessionSnapshot:
        return cls(
            session_id=data.get("session_id", ""),
            workflow_id=data.get("workflow_id", ""),
            current_state=data.get("current_state"),
            collected_data=data.get("collected_data", {}),
            menu_cache=data.get("menu_cache", {}),
        )


class WorkflowSessionStore:
    """Read/Write workflow state to Redis via `app.services.cache`."""
    
    def __init__(self):
        # We use the global cache instance
        pass

    def _key(self, session_id: str, workflow_id: str) -> str:
        return f"workflow_state:{session_id}:{workflow_id}"

    async def load(self, session_id: str, workflow_id: str) -> WorkflowSessionSnapshot:
        key = self._key(session_id, workflow_id)
        data = await cache.get(key)
        if not data:
            return WorkflowSessionSnapshot(
                session_id=session_id, workflow_id=workflow_id
            )
        return WorkflowSessionSnapshot.from_dict(data)

    async def save(self, snapshot: WorkflowSessionSnapshot) -> None:
        key = self._key(snapshot.session_id, snapshot.workflow_id)
        # Expire after 1 hour of inactivity by default
        await cache.set(key, snapshot.to_dict(), ttl=3600)

    async def clear(self, session_id: str, workflow_id: str) -> None:
        key = self._key(session_id, workflow_id)
        await cache.delete(key)
