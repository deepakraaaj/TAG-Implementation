from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class QueryContext:
    query: str
    metadata: Dict[str, Any]
    intent: Dict[str, Any]
    table: str | None = None
    filters: Dict[str, Any] = field(default_factory=dict)
    operation: str = "select"
    tenant_id: Any = None
    actor_user_id: Any = None

    @classmethod
    def from_state(cls, state: Dict[str, Any], tenant_id: Any = None, actor_user_id: Any = None) -> "QueryContext":
        messages = state.get("messages", []) or []
        query = str(messages[-1].content) if messages else ""
        metadata = dict(state.get("metadata") or {})
        intent = dict(state.get("intent") or {})
        operation = str(intent.get("operation", "select") or "select").lower()
        return cls(
            query=query,
            metadata=metadata,
            intent=intent,
            operation=operation,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
        )
