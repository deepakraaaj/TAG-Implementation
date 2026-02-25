from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple


class CacheBackend(Protocol):
    """Key-value cache contract used by chat/session services."""

    def generate_key(self, prefix: str, *args: Any) -> str:
        ...

    async def get(self, key: str) -> Optional[Any]:
        ...

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        ...

    async def delete(self, key: str) -> Any:
        ...


class ReportCacheBackend(Protocol):
    """Cache contract used by report execution flows."""

    def generate_cache_key(
        self,
        report_id: str,
        company_id: int,
        page: int,
        page_size: int,
        **params: Any,
    ) -> str:
        ...

    async def get(self, cache_key: str) -> Optional[Any]:
        ...

    async def set(self, cache_key: str, value: Any, ttl: Optional[int] = None) -> bool:
        ...


class DBGateway(Protocol):
    """Database contract for read/write operations used by services."""

    def execute_query(self, sql: str, params: Any = None) -> Any:
        ...

    def execute_update(self, sql: str, params: Any = None) -> Any:
        ...


class AuditLogger(Protocol):
    """Audit writer contract used by report execution flows."""

    async def log_report_execution(
        self,
        company_id: int,
        user_id: int,
        report_id: str,
        report_name: str,
        execution_time_ms: int,
        row_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        ...


class ChatHistoryBackend(Protocol):
    """History store contract used by chat service."""

    async def load(self, session_id: str) -> List[Dict[str, str]]:
        ...

    async def append_turn(self, session_id: str, user_message: Any, assistant_message: Any) -> List[Dict[str, str]]:
        ...


class SchemaGateway(Protocol):
    """Schema service contract used for DB engine resolution."""

    def get_engine_for_url(self, db_url: str) -> Any:
        ...


class IntentAnalyzer(Protocol):
    """Intent analyzer contract for chat/flow routing."""

    async def analyze_with_usage(self, message: str, metadata: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Any]:
        ...


class FlowBuilderGateway(Protocol):
    def resolve_table(self, message: str, intent: Dict[str, Any]) -> Any:
        ...


class FlowRegistryGateway(Protocol):
    def has(self, flow_id: str) -> bool:
        ...


class FlowSQLExecutor(Protocol):
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...


class FlowOrchestrator(Protocol):
    builder: FlowBuilderGateway
    registry: FlowRegistryGateway
    sql_executor: FlowSQLExecutor

    async def run(self, flow_id: str, state: Dict[str, Any], user_input: str, metadata: Dict[str, Any]) -> Any:
        ...


class MetricsCollector(Protocol):
    def record_chat_request(self, status: str, duration_seconds: float, source: str = "live") -> None:
        ...

    def record_chat_stage_latency(self, stage: str, duration_seconds: float) -> None:
        ...

    def record_chat_timeout(self, stage: str) -> None:
        ...

    def record_idempotency_replay(self) -> None:
        ...


class ToonCodec(Protocol):
    def encode(self, value: Any) -> Any:
        ...

    def estimate_tokens(self, content: str) -> int:
        ...


class WorkflowInvoker(Protocol):
    async def ainvoke(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        ...


WorkflowProvider = Callable[[], Optional[WorkflowInvoker]]
KVParser = Callable[[str], Dict[str, Any]]
