import json
import logging
import os
import re
import uuid
import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from app.config import get_settings
from app.domains.registry import DomainRegistry
from app.schemas.chat import ChatRequest
from app.services.interfaces import (
    CacheBackend,
    ChatHistoryBackend,
    FlowOrchestrator,
    IntentAnalyzer,
    KVParser,
    MetricsCollector,
    SchemaGateway,
    ToonCodec,
    WorkflowProvider,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class ChatService:
    _CACHE_METADATA_EXCLUDED_KEYS = frozenset(
        {
            "trace_id",
            "idempotency_key",
            "response_format",
            "output_format",
            "format",
            "session_id",
        }
    )
    _REDACTED_METADATA_TOKENS = (
        "token",
        "secret",
        "password",
        "authorization",
        "cookie",
        "api_key",
        "db_connection_string",
        "database_url",
        "redis_url",
    )
    _DEFAULT_SUMMARY_SPEC: Dict[str, Any] = {
        "entity_label": "tasks",
        "status_column": "status",
        "emit_entity_count_aliases": True,
        "status_buckets": [
            {"key": "completed", "label": "Completed", "values": ["completed", "2"]},
            {"key": "pending", "label": "Pending", "values": ["pending", "0"]},
            {"key": "in_progress", "label": "In Progress", "values": ["in progress", "1"]},
            {"key": "overdue", "label": "Overdue", "values": ["overdue", "3"]},
        ],
    }

    @staticmethod
    def _workflow_from_lifespan():
        try:
            from app.core import lifespan as core_lifespan

            return getattr(core_lifespan, "workflow", None)
        except Exception:
            return None

    @staticmethod
    def _noop_flow_engine():
        class _NoopRegistry:
            @staticmethod
            def has(_flow_id: str) -> bool:
                return False

        class _NoopBuilder:
            @staticmethod
            def resolve_table(_message: str, _intent: Dict[str, Any]) -> str:
                return ""

        class _NoopSQLExecutor:
            @staticmethod
            async def run(_payload: Dict[str, Any]) -> Dict[str, Any]:
                return {"row_count": 0, "rows_preview": []}

        class _NoopFlowEngine:
            def __init__(self):
                self.registry = _NoopRegistry()
                self.builder = _NoopBuilder()
                self.sql_executor = _NoopSQLExecutor()

            @staticmethod
            async def run(_flow_id: str, _state: Dict[str, Any], _user_input: str, _metadata: Dict[str, Any]):
                raise RuntimeError("Flow engine is not initialized.")

        return _NoopFlowEngine()

    def __init__(
        self,
        schema_service: Optional[SchemaGateway] = None,
        intent_service: Optional[IntentAnalyzer] = None,
        flow_engine: Optional[FlowOrchestrator] = None,
        history_store: Optional[ChatHistoryBackend] = None,
        metrics_service: Optional[MetricsCollector] = None,
        toon_service: Optional[ToonCodec] = None,
        cache_backend: Optional[CacheBackend] = None,
        workflow_provider: Optional[WorkflowProvider] = None,
        kv_parser: Optional[KVParser] = None,
    ):
        if cache_backend is None:
            from app.services.platform.cache import cache as default_cache

            cache_backend = default_cache

        if schema_service is None:
            try:
                from app.services.data.schema_service import SchemaService

                schema_service = SchemaService()
            except Exception:
                schema_service = type(
                    "_NoopSchema",
                    (),
                    {"get_engine_for_url": staticmethod(lambda _db_url: None)},
                )()

        if metrics_service is None:
            from app.services.observability.metrics_service import MetricsService

            metrics_service = MetricsService()

        if toon_service is None:
            from app.services.core.toon_service import ToonService

            toon_service = ToonService()

        if history_store is None:
            from app.services.chat.history_store import ChatHistoryStore

            history_store = ChatHistoryStore(cache_backend=cache_backend, ttl_seconds=86400, max_messages=100)

        if kv_parser is None:
            try:
                from app.assistant.engine.sql.sql_builder_service import SQLBuilderService

                kv_parser = SQLBuilderService.parse_kv_pairs
            except Exception:
                kv_parser = lambda _text: {}

        if intent_service is None:
            from app.assistant.engine.intent.intent_service import IntentService

            intent_service = IntentService(llm=object())

        if flow_engine is None:
            try:
                from app.assistant.nodes.sql.sql_execute_node import SQLExecuteNode
                from app.assistant.engine.flow.flow_engine import FlowEngine
                from app.assistant.engine.flow.plugins.manifest_flow_plugin import ManifestFlowPlugin
                from app.assistant.engine.flow.flow_registry import FlowRegistry
                from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog
                from app.assistant.engine.sql.sql_builder_service import SQLBuilderService

                domain_provider = DomainRegistry.get_current_domain
                manifest_catalog = ManifestCatalog(domain_provider=domain_provider)
                sql_builder_service = SQLBuilderService(
                    llm=object(),
                    manifest_catalog=manifest_catalog,
                    domain_provider=domain_provider,
                    toon_service=toon_service,
                )
                sql_executor = SQLExecuteNode(
                    schema_service=schema_service,
                    domain_provider=domain_provider,
                )
                flow_engine = FlowEngine(
                    registry=FlowRegistry(),
                    schema_service=schema_service,
                    sql_builder_service=sql_builder_service,
                    sql_executor=sql_executor,
                    plugins=[
                        ManifestFlowPlugin(
                            schema_service,
                            sql_builder_service,
                            sql_executor,
                            manifest_catalog,
                        )
                    ],
                )
            except Exception:
                flow_engine = self._noop_flow_engine()

        if workflow_provider is None:
            workflow_provider = self._workflow_from_lifespan

        self.cache = cache_backend
        self.schema = schema_service
        self.intent = intent_service
        self.flow_engine = flow_engine
        self.history_store = history_store
        self.metrics = metrics_service
        self.toon = toon_service
        self.workflow_provider = workflow_provider
        self.kv_parser = kv_parser
        self.flow_mode = str(getattr(settings, "ASSISTANT_FLOW_MODE", "yaml") or "yaml").strip().lower()
        self.workflow_timeout_seconds = max(1, int(getattr(settings, "QUERY_TIMEOUT_SECONDS", 30) or 30))
        self.sql_timeout_seconds = max(1, int(getattr(settings, "QUERY_TIMEOUT_SECONDS", 30) or 30))
        self.default_page_size = 20
        self.max_page_size = max(1, int(getattr(settings, "MAX_PAGE_SIZE", 1000) or 1000))
        if self.flow_mode != "yaml":
            self.flow_mode = "yaml"

    def _flow_state_key(self, session_id: str) -> str:
        return self.cache.generate_key("flow_state", session_id)

    def _pending_select_key(self, session_id: str) -> str:
        return self.cache.generate_key("pending_select", session_id)

    def _last_select_key(self, session_id: str) -> str:
        return self.cache.generate_key("last_select", session_id)

    def _idempotency_cache_key(self, session_id: str, idempotency_key: str) -> str:
        return self.cache.generate_key("chat_idempotent", session_id, idempotency_key)

    async def _cache_get(self, key: str, purpose: str) -> Any:
        try:
            return await self.cache.get(key)
        except Exception:
            logger.exception("Cache GET failed for %s", purpose)
            return None

    async def _cache_set(self, key: str, value: Any, ttl: int, purpose: str) -> bool:
        try:
            return bool(await self.cache.set(key, value, ttl=ttl))
        except Exception:
            logger.exception("Cache SET failed for %s", purpose)
            return False

    async def _cache_delete(self, key: str, purpose: str) -> None:
        try:
            await self.cache.delete(key)
        except Exception:
            logger.exception("Cache DELETE failed for %s", purpose)

    async def _load_history(self, session_id: str) -> List[Dict[str, str]]:
        return await self.history_store.load(session_id)

    async def _append_history_turn(self, session_id: str, user_message: Any, assistant_message: Any) -> None:
        await self.history_store.append_turn(session_id, user_message, assistant_message)

    async def _load_flow_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await self._cache_get(self._flow_state_key(session_id), "flow_state")
        return state if isinstance(state, dict) else None

    async def _save_flow_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await self._cache_set(self._flow_state_key(session_id), state, ttl=3600, purpose="flow_state")

    async def _clear_flow_state(self, session_id: str) -> None:
        await self._cache_delete(self._flow_state_key(session_id), "flow_state")

    async def _load_pending_select_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await self._cache_get(self._pending_select_key(session_id), "pending_select")
        return state if isinstance(state, dict) else None

    async def _save_pending_select_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await self._cache_set(self._pending_select_key(session_id), state, ttl=1800, purpose="pending_select")

    async def _clear_pending_select_state(self, session_id: str) -> None:
        await self._cache_delete(self._pending_select_key(session_id), "pending_select")

    async def _load_last_select_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await self._cache_get(self._last_select_key(session_id), "last_select")
        return state if isinstance(state, dict) else None

    async def _save_last_select_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await self._cache_set(self._last_select_key(session_id), state, ttl=1800, purpose="last_select")

    async def _clear_last_select_state(self, session_id: str) -> None:
        await self._cache_delete(self._last_select_key(session_id), "last_select")

    @staticmethod
    def _parse_load_more_request(text: str) -> Tuple[Optional[int], Optional[int]]:
        msg = str(text or "").strip().lower()
        if not msg:
            return None, None
        if "load more" not in msg and "next" not in msg:
            return None, None
        limit_match = re.search(r"next\s+(\d+)", msg)
        offset_match = re.search(r"offset\s*:\s*(\d+)", msg)
        limit = int(limit_match.group(1)) if limit_match else 20
        offset = int(offset_match.group(1)) if offset_match else None
        return limit, offset

    def _bounded_page_limit(self, limit: Optional[int]) -> int:
        requested = self.default_page_size if limit is None else int(limit)
        return max(1, min(requested, self.max_page_size))

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _normalize_rows_payload(rows: Any) -> List[Dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for item in rows:
            if isinstance(item, dict):
                normalized.append(dict(item))
        return normalized

    @classmethod
    def _rows_from_sql_result_payload(cls, sql_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(sql_result, dict):
            return []
        raw_rows = sql_result.get("sql_result")
        if isinstance(raw_rows, list):
            normalized = cls._normalize_rows_payload(raw_rows)
            if normalized:
                return normalized
        if isinstance(raw_rows, str):
            try:
                parsed = json.loads(raw_rows)
                normalized = cls._normalize_rows_payload(parsed)
                if normalized:
                    return normalized
            except Exception:
                pass
        return cls._normalize_rows_payload(sql_result.get("rows_preview"))

    def _normalize_last_select_progress(self, last_select_state: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int, int]:
        state = dict(last_select_state or {})
        loaded_rows = self._normalize_rows_payload(state.get("loaded_rows"))
        loaded_count = len(loaded_rows)
        if loaded_count <= 0:
            loaded_count = max(0, self._safe_int(state.get("loaded_count"), 0))
        if loaded_count <= 0:
            legacy_offset = max(0, self._safe_int(state.get("offset"), 0))
            legacy_limit = self._bounded_page_limit(self._safe_int(state.get("limit"), self.default_page_size))
            loaded_count = legacy_offset + legacy_limit
        total_records = self._safe_int(state.get("total_records"), 0)
        if total_records <= 0:
            total_records = self._safe_int(state.get("row_count"), 0)
        if total_records > 0:
            loaded_count = min(loaded_count, total_records)
            if loaded_rows and len(loaded_rows) > total_records:
                loaded_rows = loaded_rows[:total_records]
        if loaded_rows and len(loaded_rows) != loaded_count:
            loaded_count = len(loaded_rows)
        return loaded_rows, loaded_count, max(0, total_records)

    @staticmethod
    def _summary_intent_patterns() -> List[str]:
        fallback = [
            r"\bsummary\b",
            r"\bsummarize\b",
            r"\bhow many\b.*\bcomplete(d)?\b",
            r"\bcomplete(d)?\b.*\bhow many\b",
        ]
        try:
            domain = DomainRegistry.get_current_domain()
            cfg = domain.get_config_section("summary")
            patterns = [str(item).strip() for item in (cfg.get("intent_patterns") or []) if str(item).strip()]
            return patterns or fallback
        except Exception:
            return fallback

    @classmethod
    def _is_summary_request(cls, text: str) -> bool:
        msg = str(text or "").strip().lower()
        if not msg:
            return False
        return any(re.search(p, msg) for p in cls._summary_intent_patterns())

    @staticmethod
    def _is_likely_conversational_followup(text: str) -> bool:
        msg = str(text or "").strip().lower()
        if not msg:
            return False
        patterns = [
            r"^(hi|hello|hey)\b",
            r"^good\s+(morning|afternoon|evening)\b",
            r"\bwho\s+are\s+you\b",
            r"\bwhat\s+are\s+you\b",
            r"\btell\s+me\s+about\s+yourself\b",
            r"\bwhat\s+can\s+you\s+do\b",
            r"\bhelp\b",
            r"^(thanks|thank you)\b",
            r"^(ok|okay|cool|nice)\b",
        ]
        return any(re.search(pattern, msg) for pattern in patterns)

    @staticmethod
    def _is_flow_control_input(text: str) -> bool:
        msg = str(text or "").strip().lower()
        if not msg:
            return False
        if msg in {
            "back",
            "cancel",
            "stop",
            "exit",
            "abort",
            "more",
            "next",
            "prev",
            "yes",
            "y",
            "no",
            "n",
            "confirm",
            "proceed",
            "change",
            "edit",
        }:
            return True
        return msg.isdigit()

    @classmethod
    def _should_interrupt_active_flow(cls, text: str) -> bool:
        """
        Detect when an incoming message is likely a fresh request/chat turn
        and should not be treated as the next step in an active menu flow.
        """
        msg = str(text or "").strip()
        if not msg:
            return False
        if cls._is_flow_control_input(msg):
            return False

        lower_msg = msg.lower()
        if cls._is_likely_conversational_followup(lower_msg):
            return True

        if re.search(
            (
                r"^(show|list|get|find|fetch|count|report|summary|summarize|"
                r"what|which|who|when|where|why|how many|"
                r"create|add|new|update|change|edit|delete|remove|insert|select|"
                r"can you|could you|would you|please)\b"
            ),
            lower_msg,
        ):
            return True

        if re.search(r"\b[a-z_][a-z0-9_]*\s*=", lower_msg):
            return True

        return False

    @staticmethod
    def _safe_identifier(identifier: str, default: str) -> str:
        candidate = str(identifier or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            return candidate
        return default

    @staticmethod
    def _normalize_summary_spec(domain_config: Dict[str, Any]) -> Dict[str, Any]:
        fallback = dict(ChatService._DEFAULT_SUMMARY_SPEC)
        fallback["status_buckets"] = [dict(x) for x in ChatService._DEFAULT_SUMMARY_SPEC["status_buckets"]]

        cfg = dict(((domain_config or {}).get("summary") or {}))
        entity_label = str(cfg.get("entity_label", fallback["entity_label"])).strip() or fallback["entity_label"]
        status_column = ChatService._safe_identifier(
            str(cfg.get("status_column", fallback["status_column"])),
            str(fallback["status_column"]),
        )
        emit_entity_count_aliases = bool(
            cfg.get("emit_entity_count_aliases", fallback.get("emit_entity_count_aliases", False))
        )

        normalized_buckets: List[Dict[str, Any]] = []
        for bucket in cfg.get("status_buckets") or []:
            if not isinstance(bucket, dict):
                continue
            key = ChatService._safe_identifier(str(bucket.get("key", "")), "")
            label = str(bucket.get("label", "")).strip()
            values = [str(v).strip().lower() for v in (bucket.get("values") or []) if str(v).strip()]
            if key and label and values:
                normalized_buckets.append({"key": key, "label": label, "values": values})

        if not normalized_buckets:
            normalized_buckets = [dict(x) for x in fallback["status_buckets"]]

        return {
            "entity_label": entity_label,
            "status_column": status_column,
            "emit_entity_count_aliases": emit_entity_count_aliases,
            "status_buckets": normalized_buckets,
        }

    @staticmethod
    def _metric_suffix(text: str, default: str = "entity") -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")
        return normalized or default

    @staticmethod
    def _build_summary_sql(base_sql: str, spec: Dict[str, Any]) -> str:
        status_column = ChatService._safe_identifier(str(spec.get("status_column", "status")), "status")
        select_parts = ["COUNT(*) AS total_count"]
        for bucket in spec.get("status_buckets") or []:
            key = ChatService._safe_identifier(str(bucket.get("key", "")), "")
            values = [str(v).strip().lower() for v in (bucket.get("values") or []) if str(v).strip()]
            if not key or not values:
                continue
            literal_values = ",".join("'" + value.replace("'", "''") + "'" for value in values)
            select_parts.append(
                f"SUM(CASE WHEN LOWER(CAST({status_column} AS CHAR)) IN ({literal_values}) THEN 1 ELSE 0 END) AS {key}_count"
            )
        select_sql = ", ".join(select_parts)
        return f"SELECT {select_sql} FROM ({base_sql}) summary_rows"

    def _summarize_last_select(self, sql: str, metadata: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        base_sql = str(sql or "").strip().rstrip(";")
        if not base_sql:
            return None, None, None
        domain = DomainRegistry.get_current_domain()
        spec = self._normalize_summary_spec(domain.config if isinstance(domain.config, dict) else {})
        summary_sql = self._build_summary_sql(base_sql, spec)

        try:
            db_url = (metadata or {}).get("db_connection_string") or settings.DATABASE_URL
            engine = self.schema.get_engine_for_url(db_url)
            with engine.connect() as conn:
                row = conn.execute(text(summary_sql)).mappings().first() or {}

            total = int(row.get("total_count") or 0)
            metrics: List[str] = []
            rows_preview: Dict[str, Any] = {"total_count": total}
            entity_label = str(spec.get("entity_label", "records")).strip() or "records"
            emit_entity_count_aliases = bool(spec.get("emit_entity_count_aliases", False))
            entity_suffix = self._metric_suffix(entity_label, default="entity")
            for bucket in spec.get("status_buckets") or []:
                key = ChatService._safe_identifier(str(bucket.get("key", "")), "")
                label = str(bucket.get("label", "")).strip()
                if not key or not label:
                    continue
                count = int(row.get(f"{key}_count") or 0)
                rows_preview[f"{key}_count"] = count
                metrics.append(f"{label} {count}")
                if emit_entity_count_aliases:
                    rows_preview[f"{key}_{entity_suffix}"] = count

            summary_tail = ", ".join(metrics) if metrics else "No status buckets configured."
            message = f"Summary: total {entity_label} {total}. {summary_tail}."

            if emit_entity_count_aliases:
                rows_preview[f"total_{entity_suffix}"] = total

            sql_data = {
                "ran": True,
                "cached": False,
                "query": summary_sql,
                "row_count": 1,
                "rows_preview": [rows_preview],
            }
            return message, summary_sql, sql_data
        except Exception:
            return None, None, None

    @staticmethod
    def _apply_limit_offset(sql: str, limit: int, offset: int) -> str:
        base = str(sql or "").strip().rstrip(";")
        base = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base, flags=re.IGNORECASE)
        return f"{base} LIMIT {max(1, int(limit))} OFFSET {max(0, int(offset))};"

    @staticmethod
    def _select_workflow_ids() -> set[str]:
        fallback = {"select_filters"}
        try:
            domain = DomainRegistry.get_current_domain()
            cfg = domain.get_config_section("select_workflow")
            values = {
                str(item).strip()
                for item in (cfg.get("workflow_ids") or [])
                if str(item).strip()
            }
            workflow_id = str(cfg.get("workflow_id", "")).strip()
            if workflow_id:
                values.add(workflow_id)
            return values or fallback
        except Exception:
            return fallback

    @classmethod
    def _pending_select_from_workflow_payload(cls, workflow_payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(workflow_payload, dict):
            return None
        workflow_id = str(workflow_payload.get("workflow_id", "")).strip()
        if not workflow_id or workflow_id not in cls._select_workflow_ids():
            return None
        table = str((workflow_payload.get("collected_data") or {}).get("table", "")).strip()
        if not table:
            return None
        collected_fields = dict((workflow_payload.get("collected_data") or {}).get("collected_fields") or {})
        return {"table": table, "filters": collected_fields}

    @staticmethod
    def _merge_pending_select(
        pending_select: Any,
        workflow_payload: Any,
    ) -> Optional[Dict[str, Any]]:
        pending_from_workflow = ChatService._pending_select_from_workflow_payload(workflow_payload)
        if isinstance(pending_select, dict):
            merged = dict(pending_select)
            if not str(merged.get("table", "")).strip() and isinstance(pending_from_workflow, dict):
                merged["table"] = str(pending_from_workflow.get("table", "")).strip()
            if (
                (not isinstance(merged.get("filters"), dict) or not merged.get("filters"))
                and isinstance(pending_from_workflow, dict)
            ):
                merged["filters"] = dict(pending_from_workflow.get("filters") or {})
            return merged
        if isinstance(pending_from_workflow, dict):
            return pending_from_workflow
        return None

    async def _persist_pending_select_state(self, session_id: str, pending_select: Any) -> None:
        if isinstance(pending_select, dict) and str(pending_select.get("table", "")).strip():
            await self._save_pending_select_state(session_id, pending_select)
            return
        await self._clear_pending_select_state(session_id)

    async def _run_with_timeout(self, operation_name: str, awaitable: Any, timeout_seconds: int) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=max(1, int(timeout_seconds or 1)))
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{operation_name} timed out after {int(timeout_seconds)} seconds") from exc

    @staticmethod
    def _mark_stage(timings: Dict[str, float], stage: str, started_at: float) -> None:
        timings[str(stage)] = round((time.perf_counter() - float(started_at)) * 1000, 2)

    @staticmethod
    def _stage_timings_payload(timings: Dict[str, float], started_at: float) -> Dict[str, float]:
        payload = dict(timings or {})
        payload["total"] = round((time.perf_counter() - float(started_at)) * 1000, 2)
        return payload

    @staticmethod
    def _response_format(metadata: Optional[Dict[str, Any]]) -> str:
        meta = metadata if isinstance(metadata, dict) else {}
        fmt = str(
            meta.get("response_format")
            or meta.get("output_format")
            or meta.get("format")
            or ""
        ).strip().lower()
        return fmt or "json"

    @classmethod
    def _cacheable_metadata(cls, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        filtered: Dict[str, Any] = {}
        for key, value in metadata.items():
            normalized_key = str(key or "").strip()
            if not normalized_key or normalized_key.startswith("_"):
                continue
            if normalized_key in cls._CACHE_METADATA_EXCLUDED_KEYS:
                continue
            filtered[normalized_key] = value
        return filtered

    @staticmethod
    def _idempotency_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        filtered: Dict[str, Any] = {}
        for key, value in metadata.items():
            normalized_key = str(key or "").strip()
            if not normalized_key or normalized_key.startswith("_"):
                continue
            if normalized_key in {"trace_id", "idempotency_key", "session_id"}:
                continue
            filtered[normalized_key] = value
        return filtered

    @classmethod
    def _loggable_metadata(cls, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        safe: Dict[str, Any] = {}
        for key, value in metadata.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            if normalized_key.startswith("_"):
                continue
            lowered_key = normalized_key.lower()
            if any(token in lowered_key for token in cls._REDACTED_METADATA_TOKENS):
                safe[normalized_key] = "[redacted]"
                continue
            if isinstance(value, str):
                safe[normalized_key] = value if len(value) <= 200 else f"{value[:197]}..."
                continue
            safe[normalized_key] = value
        return safe

    @classmethod
    def _wants_toon(cls, metadata: Optional[Dict[str, Any]]) -> bool:
        fmt = cls._response_format(metadata)
        return fmt in {"toon", "both", "json+toon", "toon+json"}

    def _decorate_sql_payload_for_format(
        self,
        sql_payload: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not isinstance(sql_payload, dict):
            return sql_payload

        rows_preview = sql_payload.get("rows_preview")
        if not isinstance(rows_preview, list):
            return sql_payload

        decorated = dict(sql_payload)
        if not rows_preview:
            decorated["rows_preview_token_count_without_toon"] = 0
            decorated["rows_preview_token_count_with_toon"] = 0
            decorated["rows_preview_token_saved"] = 0
            decorated["rows_preview_token_saved_percent"] = 0.0
            if self._wants_toon(metadata):
                decorated["rows_preview_toon"] = self.toon.encode(rows_preview)
                decorated["rows_preview_encoding"] = "toon"
            return decorated

        try:
            rows_preview_toon = self.toon.encode(rows_preview)
            rows_preview_json = json.dumps(rows_preview, separators=(",", ":"), ensure_ascii=False, default=str)
            json_tokens = self.toon.estimate_tokens(rows_preview_json)
            toon_tokens = self.toon.estimate_tokens(rows_preview_toon)
            delta = json_tokens - toon_tokens
            percent = (float(delta) / float(json_tokens) * 100.0) if json_tokens > 0 else 0.0

            decorated["rows_preview_token_count_without_toon"] = json_tokens
            decorated["rows_preview_token_count_with_toon"] = toon_tokens
            decorated["rows_preview_token_saved"] = delta
            decorated["rows_preview_token_saved_percent"] = round(percent, 2)
            if self._wants_toon(metadata):
                decorated["rows_preview_toon"] = rows_preview_toon
                decorated["rows_preview_encoding"] = "toon"
        except Exception:
            # Keep original payload on formatter failure.
            return sql_payload
        return decorated

    @classmethod
    def _append_toon_token_summary_to_message(
        cls,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return payload

        sql_payload = payload.get("sql")
        if not isinstance(sql_payload, dict):
            return payload

        without_toon = sql_payload.get("rows_preview_token_count_without_toon")
        with_toon = sql_payload.get("rows_preview_token_count_with_toon")
        try:
            without_toon_count = int(without_toon)
            with_toon_count = int(with_toon)
        except Exception:
            return payload
        if without_toon_count <= 0 and with_toon_count <= 0:
            return payload

        summary = (
            f"Token estimate for preview: with TOON {with_toon_count}, "
            f"without TOON {without_toon_count}."
        )
        sql_payload["rows_preview_token_summary"] = summary
        payload["sql"] = sql_payload
        return payload

    @classmethod
    def _append_llm_token_summary_to_message(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return payload

        usage = payload.get("token_usage")
        if not isinstance(usage, dict):
            return payload

        with_toon_count = int(usage.get("prompt_tokens_est_with_toon") or 0)
        without_toon_count = int(usage.get("prompt_tokens_est_without_toon") or 0)
        saved_count = int(usage.get("prompt_tokens_est_saved") or 0)
        llm_calls_count = int(usage.get("llm_calls") or 0)
        llm_calls_skipped = int(usage.get("llm_calls_skipped") or 0)
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0)

        if (
            with_toon_count <= 0
            and without_toon_count <= 0
            and llm_calls_count <= 0
            and llm_calls_skipped <= 0
        ):
            return payload

        summary = (
            f"LLM prompt token estimate: with TOON {with_toon_count}, "
            f"without TOON {without_toon_count}."
        )
        if saved_count > 0:
            summary += f" Saved {saved_count} tokens."
        if llm_calls_count > 0:
            summary += f" LLM calls: {llm_calls_count}."
        if total_tokens > 0:
            summary += (
                f" Actual usage -> prompt {prompt_tokens}, completion {completion_tokens}, "
                f"total {total_tokens}."
            )
        if llm_calls_skipped > 0:
            summary += f" Skipped LLM calls: {llm_calls_skipped}."
        payload["token_details"] = {
            "llm_prompt_token_summary": summary,
            "prompt_tokens_est_with_toon": with_toon_count,
            "prompt_tokens_est_without_toon": without_toon_count,
            "prompt_tokens_est_saved": saved_count,
            "llm_calls": llm_calls_count,
            "llm_calls_skipped": llm_calls_skipped,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        return payload

    def _request_idempotency_key(self, request: ChatRequest) -> str:
        direct = str(getattr(request, "idempotency_key", "") or "").strip()
        if direct:
            return direct
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        return str(metadata.get("idempotency_key", "") or "").strip()

    def _request_fingerprint(self, request: ChatRequest) -> str:
        return self.cache.generate_key(
            "chat_request",
            str(request.session_id or "").strip(),
            str(request.message or "").strip(),
            self._idempotency_metadata(request.metadata),
        )

    def _chat_cache_key(self, request: ChatRequest, history_payload: List[Dict[str, str]]) -> str:
        return self.cache.generate_key(
            "chat",
            str(request.session_id or "").strip(),
            str(request.message or "").strip(),
            history_payload,
            self._cacheable_metadata(request.metadata),
        )

    @staticmethod
    def _trim_context_text(value: Any, max_chars: int = 240) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max(1, max_chars - 3)].rstrip() + "..."

    @classmethod
    def _recent_conversation_window(
        cls,
        history_payload: Any,
        max_turns: int = 5,
        max_chars_per_message: int = 240,
    ) -> List[Dict[str, str]]:
        if not isinstance(history_payload, list):
            return []
        max_messages = max(1, int(max_turns or 1)) * 2
        window = history_payload[-max_messages:]
        normalized: List[Dict[str, str]] = []
        for item in window:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = cls._trim_context_text(item.get("content"), max_chars=max_chars_per_message)
            if not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _recent_conversation_text(recent_conversation: Any) -> str:
        if not isinstance(recent_conversation, list):
            return ""
        lines: List[str] = []
        for item in recent_conversation:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            prefix = "User" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    async def _load_idempotent_response(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        key = self._request_idempotency_key(request)
        if not key:
            return None
        response = await self._cache_get(
            self._idempotency_cache_key(request.session_id, key),
            "idempotent_response",
        )
        if not isinstance(response, dict):
            return None
        payload = dict(response)
        stored_fingerprint = str(payload.pop("_request_fingerprint", "") or "").strip()
        current_fingerprint = self._request_fingerprint(request)
        if stored_fingerprint and stored_fingerprint != current_fingerprint:
            logger.warning(
                "Ignoring idempotent replay due to request fingerprint mismatch for session %s",
                request.session_id,
            )
            return None
        return payload

    async def _store_idempotent_response(self, request: ChatRequest, response_payload: Dict[str, Any]) -> None:
        key = self._request_idempotency_key(request)
        if not key or not isinstance(response_payload, dict):
            return
        payload = dict(response_payload)
        payload["_request_fingerprint"] = self._request_fingerprint(request)
        await self._cache_set(
            self._idempotency_cache_key(request.session_id, key),
            payload,
            ttl=3600,
            purpose="idempotent_response",
        )

    @staticmethod
    def _json_line(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, default=str) + "\n"

    @classmethod
    def _token_line(cls, message: str) -> str:
        return cls._json_line({"type": "token", "content": str(message)})

    @classmethod
    def _error_line(cls, message: str) -> str:
        return cls._json_line({"type": "error", "message": str(message)})

    def _result_line(
        self,
        session_id: str,
        message: str,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        report_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        pending_select: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> str:
        return self._json_line(
            self._build_final_response(
                session_id,
                message,
                status=status,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                report_data=report_data,
                token_usage=token_usage,
                pending_select=pending_select,
                trace_id=trace_id,
            )
        )

    @staticmethod
    def _configured_model_name() -> str:
        raw_model = str(os.getenv("LLM_MODEL", getattr(settings, "LLM_MODEL", "")) or "").strip()
        if not raw_model:
            return ""
        normalized = raw_model.replace("\\", "/").rstrip("/")
        if "/" in normalized:
            return normalized.rsplit("/", 1)[-1].strip()
        return raw_model

    @classmethod
    def _hydrate_response_metadata(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        hydrated = dict(payload or {})
        hydrated["provider_used"] = str(hydrated.get("provider_used") or "tag_backend").strip() or "tag_backend"
        hydrated["llm_model"] = str(hydrated.get("llm_model") or cls._configured_model_name()).strip()
        return hydrated

    async def _emit_token_and_result(
        self,
        request: ChatRequest,
        message: str,
        final_response: Optional[Dict[str, Any]] = None,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        report_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        pending_select: Optional[Dict[str, Any]] = None,
        stage_timings: Optional[Dict[str, float]] = None,
        trace_id: str = "",
        fallback_token: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        resolved_trace_id = str(trace_id or (request.metadata or {}).get("trace_id") or "").strip()
        if isinstance(final_response, dict):
            payload = self._hydrate_response_metadata(final_response)
        else:
            payload = self._build_final_response(
                request.session_id,
                str(message or ""),
                status=status,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                report_data=report_data,
                token_usage=token_usage,
                pending_select=pending_select,
                stage_timings=stage_timings,
                trace_id=resolved_trace_id,
            )

        payload["sql"] = self._decorate_sql_payload_for_format(
            payload.get("sql"),
            metadata=request.metadata,
        )
        payload = self._append_toon_token_summary_to_message(payload, metadata=request.metadata)
        payload = self._append_llm_token_summary_to_message(payload)

        token_message = str(payload.get("message") or message or "").strip()
        if token_message:
            yield self._token_line(token_message)
        elif fallback_token:
            yield self._token_line(str(fallback_token))
        await self._append_history_turn(request.session_id, request.message, token_message or str(message or ""))
        self._record_chat_terminal_metrics(
            status=str(payload.get("status", status)),
            stage_timings=stage_timings,
            source="live",
            error_message=str(payload.get("message", "")),
        )
        if isinstance(stage_timings, dict) and stage_timings:
            payload["stage_timings_ms"] = dict(stage_timings)
        await self._store_idempotent_response(request, payload)
        yield self._json_line(payload)

    async def _emit_error_and_result(
        self,
        session_id: str,
        error_message: str,
        request: Optional[ChatRequest] = None,
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        report_data: Optional[Dict[str, Any]] = None,
        stage_timings: Optional[Dict[str, float]] = None,
        trace_id: str = "",
    ) -> AsyncGenerator[str, None]:
        resolved_trace_id = str(trace_id or ((request.metadata or {}).get("trace_id") if request else "") or "").strip()
        yield self._error_line(error_message)
        payload = self._build_final_response(
            session_id,
            error_message,
            status="error",
            workflow_payload=workflow_payload,
            sql_data=sql_data,
            report_data=report_data,
            stage_timings=stage_timings,
            trace_id=resolved_trace_id,
        )
        payload = self._hydrate_response_metadata(payload)
        payload["sql"] = self._decorate_sql_payload_for_format(
            payload.get("sql"),
            metadata=(request.metadata if request else None),
        )
        payload = self._append_toon_token_summary_to_message(
            payload,
            metadata=(request.metadata if request else None),
        )
        payload = self._append_llm_token_summary_to_message(payload)
        self._record_chat_terminal_metrics(
            status="error",
            stage_timings=stage_timings,
            source="live",
            error_message=error_message,
        )
        if request is not None:
            await self._store_idempotent_response(request, payload)
        yield self._json_line(payload)

    def _record_chat_terminal_metrics(
        self,
        status: str,
        stage_timings: Optional[Dict[str, float]] = None,
        source: str = "live",
        error_message: str = "",
    ) -> None:
        total_ms = float((stage_timings or {}).get("total", 0.0) or 0.0)
        self.metrics.record_chat_request(status=str(status), duration_seconds=(total_ms / 1000.0), source=source)
        for stage, value_ms in (stage_timings or {}).items():
            if str(stage) == "total":
                continue
            self.metrics.record_chat_stage_latency(str(stage), float(value_ms or 0.0) / 1000.0)
        msg = str(error_message or "").lower()
        if "timed out" in msg:
            stage = msg.split("timed out", 1)[0].strip().replace(" ", "_") or "unknown"
            self.metrics.record_chat_timeout(stage=stage)

    @classmethod
    def _build_final_response(
        cls,
        session_id: str,
        message: str,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        report_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        pending_select: Optional[Dict[str, Any]] = None,
        stage_timings: Optional[Dict[str, float]] = None,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        response = cls._hydrate_response_metadata({
            "type": "result",
            "session_id": session_id,
            "status": status,
            "labels": [],
            "workflow": workflow_payload,
            "sql": sql_data,
            "report": report_data,
            "report_result": report_data,
            "token_usage": token_usage,
            "pending_select": pending_select,
            "trace_id": str(trace_id or "").strip(),
        })
        if str(message).strip():
            response["message"] = str(message)
        if isinstance(stage_timings, dict) and stage_timings:
            response["stage_timings_ms"] = dict(stage_timings)
        return response

    @staticmethod
    def _infer_pending_select_from_sql_payload(sql_data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(sql_data, dict):
            return None
        sql_query = str(sql_data.get("query", "") or "").strip()
        if not sql_query:
            return None
        upper_sql = sql_query.upper()
        if "NOT IN" not in upper_sql:
            return None
        from_tables = re.findall(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)\b", sql_query, flags=re.IGNORECASE)
        if len(from_tables) < 2:
            return None
        subject_table = str(from_tables[0]).strip().lower()
        object_table = str(from_tables[1]).strip().lower()
        if not subject_table or not object_table or subject_table == object_table:
            return None
        date_scope = ""
        if re.search(r"\bDATE_SUB\s*\(\s*CURDATE\(\)\s*,\s*INTERVAL\s+1\s+DAY\s*\)", sql_query, flags=re.IGNORECASE):
            date_scope = "yesterday"
        elif re.search(r"\bCURDATE\s*\(\s*\)", sql_query, flags=re.IGNORECASE):
            date_scope = "today"
        return {
            "table": subject_table,
            "negation": {
                "subject_table": subject_table,
                "object_table": object_table,
                "date_scope": date_scope,
            },
        }

    def _extract_invalid_column(error_message: str) -> str:
        match = re.search(r"for column '([^']+)'", str(error_message))
        return str(match.group(1)).strip() if match else ""

    @staticmethod
    def _extract_missing_required_column(error_message: str) -> str:
        match = re.search(r"Field '([^']+)' doesn't have a default value", str(error_message))
        return str(match.group(1)).strip() if match else ""

    @staticmethod
    def _domain_flow_bindings(domain: DomainRegistry) -> List[Dict[str, str]]:
        config = domain.config if isinstance(domain.config, dict) else {}
        bindings = config.get("flow_bindings")
        if isinstance(bindings, list):
            normalized: List[Dict[str, str]] = []
            for item in bindings:
                if not isinstance(item, dict):
                    continue
                flow_id = str(item.get("flow_id", "")).strip()
                if not flow_id:
                    continue
                normalized.append(
                    {
                        "flow_id": flow_id,
                        "table": str(item.get("table", "")).strip(),
                        "operation": str(item.get("operation", "")).strip().lower(),
                    }
                )
            if normalized:
                return normalized

        # Backward compatibility: allow old flows_enabled configs.
        enabled = config.get("flows_enabled")
        fallback: List[Dict[str, str]] = []
        if isinstance(enabled, list):
            for flow_id in enabled:
                flow_name = str(flow_id or "").strip()
                if flow_name:
                    fallback.append({"flow_id": flow_name, "table": "", "operation": ""})
        return fallback

    @staticmethod
    def _select_flow_binding(
        bindings: List[Dict[str, str]],
        table: str,
        operation: str,
    ) -> Optional[str]:
        normalized_table = str(table or "").strip()
        normalized_op = str(operation or "").strip().lower()
        for item in bindings:
            item_table = str(item.get("table", "")).strip()
            item_operation = str(item.get("operation", "")).strip().lower()
            if item_table and item_table != normalized_table:
                continue
            if item_operation and item_operation != normalized_op:
                continue
            flow_id = str(item.get("flow_id", "")).strip()
            if flow_id:
                return flow_id
        return None

    @staticmethod
    def _is_read_query_message(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return True
        return bool(re.search(r"\b(show|list|get|find|view|count|summary|summarize|how many|what|which)\b", text))

    @classmethod
    def _select_flow_binding_for_message(
        cls,
        bindings: List[Dict[str, str]],
        domain: DomainRegistry,
        message: str,
        table: str,
        operation: str,
    ) -> Optional[Dict[str, str]]:
        normalized_table = str(table or "").strip()
        normalized_op = cls._normalize_operation(operation, default="select")

        def _candidate_from(item: Dict[str, str], forced_operation: str = "") -> Optional[Dict[str, str]]:
            flow_id = str(item.get("flow_id", "")).strip()
            if not flow_id:
                return None
            item_table = str(item.get("table", "")).strip()
            item_operation = cls._normalize_operation(str(item.get("operation", "")).strip(), default="")
            candidate_table = item_table or normalized_table
            candidate_operation = cls._normalize_operation(
                forced_operation or item_operation or normalized_op,
                default="select",
            )
            if not candidate_table:
                return None
            if not domain.is_flow_candidate(message, candidate_table):
                return None
            return {
                "flow_id": flow_id,
                "table": candidate_table,
                "operation": candidate_operation,
            }

        # 1) Strict table+operation match.
        if normalized_table:
            for item in bindings:
                item_table = str(item.get("table", "")).strip()
                item_operation = cls._normalize_operation(str(item.get("operation", "")).strip(), default="")
                if item_table and item_table != normalized_table:
                    continue
                if item_operation and item_operation != normalized_op:
                    continue
                candidate = _candidate_from(item)
                if candidate:
                    return candidate

        # 2) Message-flow candidate with matching operation.
        for item in bindings:
            item_operation = cls._normalize_operation(str(item.get("operation", "")).strip(), default="")
            if item_operation and item_operation != normalized_op:
                continue
            candidate = _candidate_from(item)
            if candidate:
                return candidate

        # 3) If only one non-select flow candidate matches the message, route there for non-query phrasing.
        if normalized_op == "select" and not cls._is_read_query_message(message):
            relaxed_candidates: List[Dict[str, str]] = []
            seen: set[tuple[str, str, str]] = set()
            for item in bindings:
                item_operation = cls._normalize_operation(str(item.get("operation", "")).strip(), default="")
                if item_operation in {"", "select"}:
                    continue
                candidate = _candidate_from(item)
                if not candidate:
                    continue
                key = (
                    str(candidate.get("flow_id", "")),
                    str(candidate.get("table", "")),
                    str(candidate.get("operation", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                relaxed_candidates.append(candidate)
            if len(relaxed_candidates) == 1:
                return relaxed_candidates[0]

        return None

    @staticmethod
    def _normalize_operation(operation: str, default: str = "select") -> str:
        op = str(operation or "").strip().lower()
        return op if op in {"select", "insert", "update", "delete"} else str(default or "select")

    def _yaml_flow_enabled(self) -> bool:
        return self.flow_mode == "yaml"

    @staticmethod
    def _domain_normalize_flow_fields(table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        try:
            domain = DomainRegistry.get_current_domain()
        except Exception:
            return dict(fields or {})
        normalize = getattr(domain, "normalize_flow_fields", None)
        if callable(normalize):
            try:
                payload = normalize(str(table or "").strip(), dict(fields or {}))
                return dict(payload) if isinstance(payload, dict) else dict(fields or {})
            except Exception:
                return dict(fields or {})
        return dict(fields or {})

    @staticmethod
    def _domain_resolve_flow_slot_prefill(
        message: str,
        table: str,
        operation: str,
        initial_fields: Dict[str, Any],
        allow_message_fallback: bool = True,
    ) -> Dict[str, Any]:
        empty_payload = {"values": {}, "search": {}, "llm_slots_present": False}
        try:
            domain = DomainRegistry.get_current_domain()
        except Exception:
            return empty_payload
        resolver = getattr(domain, "resolve_flow_slot_prefill", None)
        if not callable(resolver):
            return empty_payload
        try:
            payload = resolver(
                str(message or ""),
                str(table or "").strip(),
                str(operation or "").strip().lower(),
                dict(initial_fields or {}),
                bool(allow_message_fallback),
            )
        except Exception:
            return empty_payload
        if not isinstance(payload, dict):
            return empty_payload
        values = payload.get("values")
        search = payload.get("search")
        return {
            "values": dict(values) if isinstance(values, dict) else {},
            "search": dict(search) if isinstance(search, dict) else {},
            "llm_slots_present": bool(payload.get("llm_slots_present", False)),
        }

    @classmethod
    def _extract_flow_prefill_hints_from_message(
        cls,
        message: str,
        table: str,
        operation: str = "insert",
    ) -> Dict[str, str]:
        payload = cls._domain_resolve_flow_slot_prefill(
            message,
            table,
            operation,
            {},
            allow_message_fallback=True,
        )
        hints = payload.get("search")
        return dict(hints) if isinstance(hints, dict) else {}

    @classmethod
    def _normalize_flow_fields(cls, table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return cls._domain_normalize_flow_fields(table, fields)

    @staticmethod
    def _flow_slot_fields_from_definition(flow_def: Dict[str, Any]) -> List[str]:
        fields = set()
        for item in flow_def.get("required_fields") or []:
            key = str(item or "").strip()
            if key:
                fields.add(key)
        for item in dict(flow_def.get("field_map") or {}).keys():
            key = str(item or "").strip()
            if key:
                fields.add(key)
        for state_def in dict(flow_def.get("states") or {}).values():
            if not isinstance(state_def, dict):
                continue
            capture = str(state_def.get("capture", "")).strip()
            if capture:
                fields.add(capture)
        return sorted(fields)

    def _flow_slot_fields(self, flow_id: str) -> List[str]:
        try:
            flow_def = self.flow_engine.registry.get(str(flow_id or "").strip())
        except Exception:
            return []
        if not isinstance(flow_def, dict):
            return []
        return self._flow_slot_fields_from_definition(flow_def)

    def _build_flow_intent_field_hint(self, flow_id: str, table: str) -> str:
        slot_fields = self._flow_slot_fields(flow_id)
        if not slot_fields:
            return ""
        fields_csv = ", ".join(slot_fields)
        return (
            f"This request is for flow `{flow_id}` on table `{table}`. "
            f"When present in user text or recent conversation, extract values into `fields` using these keys: {fields_csv}. "
            "For lookup fields, return the best name/text value when an ID is not explicitly provided."
        )

    @staticmethod
    def _merge_intent_payload(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        incoming = dict(overlay or {})
        for key in ("operation", "table"):
            value = str(incoming.get(key, "")).strip()
            if value:
                merged[key] = value
        merged_filters: Dict[str, Any] = {}
        if isinstance(base.get("filters"), dict):
            merged_filters.update(base.get("filters") or {})
        if isinstance(incoming.get("filters"), dict):
            merged_filters.update(incoming.get("filters") or {})
        merged_fields: Dict[str, Any] = {}
        if isinstance(base.get("fields"), dict):
            merged_fields.update(base.get("fields") or {})
        if isinstance(incoming.get("fields"), dict):
            merged_fields.update(incoming.get("fields") or {})
        merged["filters"] = merged_filters
        merged["fields"] = merged_fields
        return merged

    async def _enrich_intent_for_selected_flow(
        self,
        message: str,
        metadata: Dict[str, Any],
        flow_id: str,
        table: str,
        base_intent: Dict[str, Any],
    ) -> Dict[str, Any]:
        intent_hint = self._build_flow_intent_field_hint(flow_id, table)
        if not intent_hint:
            return dict(base_intent or {})
        llm_metadata = dict(metadata or {})
        llm_metadata["token_minimization"] = False
        llm_metadata["_intent_force_llm"] = True
        llm_metadata["_intent_fields_hint"] = intent_hint
        if table and not str(llm_metadata.get("pending_select_table", "")).strip():
            llm_metadata["pending_select_table"] = table
        try:
            enriched_intent, _usage = await self.intent.analyze_with_usage(message, metadata=llm_metadata)
        except Exception:
            return dict(base_intent or {})
        if not isinstance(enriched_intent, dict):
            return dict(base_intent or {})
        return self._merge_intent_payload(dict(base_intent or {}), enriched_intent)

    @classmethod
    def _flow_prefill_values(
        cls,
        message: str,
        table: str,
        operation: str,
        initial_fields: Dict[str, Any],
        allow_message_fallback: bool = True,
    ) -> Dict[str, str]:
        payload = cls._domain_resolve_flow_slot_prefill(
            message,
            table,
            operation,
            initial_fields,
            allow_message_fallback=allow_message_fallback,
        )
        values = payload.get("values")
        return dict(values) if isinstance(values, dict) else {}

    @classmethod
    def _flow_prefill_search_hints(
        cls,
        message: str,
        table: str,
        operation: str,
        initial_fields: Dict[str, Any],
        allow_message_fallback: bool = True,
    ) -> Dict[str, str]:
        payload = cls._domain_resolve_flow_slot_prefill(
            message,
            table,
            operation,
            initial_fields,
            allow_message_fallback=allow_message_fallback,
        )
        search = payload.get("search")
        return dict(search) if isinstance(search, dict) else {}

    def _resolve_workflow(self):
        workflow = None
        provider = getattr(self, "workflow_provider", None)
        if callable(provider):
            try:
                workflow = provider()
            except Exception:
                workflow = None
        if workflow is None:
            workflow = self._workflow_from_lifespan()
        return workflow

    async def _maybe_start_yaml_flow(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        if not self._yaml_flow_enabled():
            return None

        if request.metadata is None:
            request.metadata = {}

        message = str(request.message or "").strip()
        if not message:
            return None

        intent, _flow_intent_usage = await self.intent.analyze_with_usage(message, metadata=request.metadata)
        operation = self._normalize_operation(str(intent.get("operation", "select")).strip().lower(), default="select")
        table = str(self.flow_engine.builder.resolve_table(message, intent) or "").strip()
        domain = DomainRegistry.get_current_domain()

        bindings = self._domain_flow_bindings(domain)
        selected_binding = self._select_flow_binding_for_message(
            bindings,
            domain,
            message,
            table,
            operation,
        )
        if not isinstance(selected_binding, dict):
            return None
        flow_id = str(selected_binding.get("flow_id", "")).strip()
        table = str(selected_binding.get("table", "")).strip() or table
        binding_operation = self._normalize_operation(
            str(selected_binding.get("operation", "")).strip(),
            default="select",
        )
        if not self.flow_engine.registry.has(flow_id):
            logger.warning("Flow `%s` is not available.", flow_id)
            return None

        resolved_operation = self._normalize_operation(
            operation,
            default=self._normalize_operation(binding_operation, default="select"),
        )

        if resolved_operation == "insert":
            intent = await self._enrich_intent_for_selected_flow(
                message,
                dict(request.metadata or {}),
                flow_id,
                table,
                intent,
            )

        initial_fields: Dict[str, Any] = {}
        if isinstance(intent.get("fields"), dict):
            initial_fields.update(intent.get("fields") or {})
        initial_fields = self._domain_normalize_flow_fields(table, initial_fields)

        initial_fields.update(self.kv_parser(message))
        initial_fields = self._domain_normalize_flow_fields(table, initial_fields)

        prefill_payload = self._domain_resolve_flow_slot_prefill(
            message,
            table,
            resolved_operation,
            initial_fields,
            allow_message_fallback=True,
        )
        prefill_values = dict(prefill_payload.get("values") or {})
        initial_fields.update(
            prefill_values
        )
        prefill_search = dict(prefill_payload.get("search") or {})

        flow_state = {
            "active_flow": flow_id,
            "current_state": "",
            "flow_context": {
                "operation": resolved_operation,
                "table": table,
                "values": initial_fields,
                "prefill_search": prefill_search,
                "history": [],
                "metadata": dict(request.metadata or {}),
            },
        }

        try:
            result = await self._run_with_timeout(
                "Flow startup",
                self.flow_engine.run(flow_id, flow_state, "", dict(request.metadata or {})),
                self.workflow_timeout_seconds,
            )
        except Exception as exc:
            logger.error("Flow startup failed: %s", exc)
            return self._build_final_response(
                request.session_id,
                str(exc),
                status="error",
                workflow_payload=None,
                sql_data=None,
                trace_id=str((request.metadata or {}).get("trace_id", "") or ""),
            )
        if result.clear_state or result.completed:
            await self._clear_flow_state(request.session_id)
        else:
            await self._save_flow_state(request.session_id, flow_state)

        return self._build_final_response(
            request.session_id,
            result.message,
            status=result.status,
            workflow_payload=result.workflow,
            sql_data=result.sql_data,
            trace_id=str((request.metadata or {}).get("trace_id", "") or ""),
        )

    async def _handle_active_flow(self, request: ChatRequest, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        if request.metadata is None:
            request.metadata = {}

        flow_id = str(flow_state.get("active_flow", "")).strip()
        if not flow_id:
            await self._clear_flow_state(request.session_id)
            return self._build_final_response(
                request.session_id,
                "Flow state is invalid. Please start again.",
                status="error",
                trace_id=str((request.metadata or {}).get("trace_id", "") or ""),
            )

        before_values = dict(((flow_state.get("flow_context") or {}).get("values") or {}))
        before_display_values = dict(((flow_state.get("flow_context") or {}).get("display_values") or {}))
        try:
            result = await self._run_with_timeout(
                "Flow continuation",
                self.flow_engine.run(
                    flow_id,
                    flow_state,
                    str(request.message or ""),
                    dict(request.metadata or {}),
                ),
                self.workflow_timeout_seconds,
            )
        except Exception as exc:
            logger.error("Flow continuation failed: %s", exc)
            return self._build_final_response(
                request.session_id,
                str(exc),
                status="error",
                workflow_payload=None,
                sql_data=None,
                trace_id=str((request.metadata or {}).get("trace_id", "") or ""),
            )

        resolved_user_input = self._resolve_flow_input_for_history(
            str(request.message or ""),
            flow_state,
            before_values,
            before_display_values,
        )

        if result.clear_state or result.completed:
            await self._clear_flow_state(request.session_id)
        else:
            await self._save_flow_state(request.session_id, flow_state)

        response = self._build_final_response(
            request.session_id,
            result.message,
            status=result.status,
            workflow_payload=result.workflow,
            sql_data=result.sql_data,
            trace_id=str((request.metadata or {}).get("trace_id", "") or ""),
        )
        if resolved_user_input:
            response["_display_user_input"] = resolved_user_input
        return response

    @staticmethod
    def _resolve_flow_input_for_history(
        raw_input: str,
        flow_state: Dict[str, Any],
        before_values: Dict[str, Any],
        before_display_values: Dict[str, Any],
    ) -> str:
        """
        Keep transcript user text human-readable by replacing raw internal values
        with the resolved option label when a single menu field changed.
        """
        incoming = str(raw_input or "").strip()
        if not incoming:
            return ""

        flow_context = dict(flow_state.get("flow_context") or {})
        after_values = dict(flow_context.get("values") or {})
        after_display_values = dict(flow_context.get("display_values") or {})
        changed_keys = [key for key, value in after_values.items() if before_values.get(key) != value]
        if len(changed_keys) != 1:
            return ""

        changed_key = changed_keys[0]
        new_value = str(after_values.get(changed_key, "")).strip()
        if not new_value:
            return ""
        new_label = str(after_display_values.get(changed_key, "")).strip()
        if not new_label or new_label == new_value:
            return ""

        incoming_lower = incoming.lower()
        was_value_like = incoming_lower == new_value.lower()
        was_numeric_menu_pick = incoming.isdigit()
        if was_value_like or was_numeric_menu_pick:
            # Avoid rewriting if the user already typed a readable label.
            previous_label = str(before_display_values.get(changed_key, "")).strip().lower()
            if previous_label and incoming_lower == previous_label:
                return ""
            return new_label
        return ""

    async def start_session(self):
        return {"session_id": str(uuid.uuid4()), "message": "Session started"}

    async def generate_chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        workflow = self._resolve_workflow()
        stream_started_at = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        if request.metadata is None:
            request.metadata = {}

        try:
            endpoint_pre_stream_ms = float(request.metadata.pop("_endpoint_pre_stream_ms", 0.0) or 0.0)
            if endpoint_pre_stream_ms > 0:
                stage_timings["endpoint_pre_stream"] = round(endpoint_pre_stream_ms, 2)
        except Exception:
            pass
        try:
            user_lookup_ms = float(request.metadata.pop("_user_lookup_ms", 0.0) or 0.0)
            if user_lookup_ms > 0:
                stage_timings["user_lookup"] = round(user_lookup_ms, 2)
        except Exception:
            pass

        request.metadata["trace_id"] = str(request.metadata.get("trace_id") or uuid.uuid4().hex).strip()
        trace_id = str(request.metadata.get("trace_id") or "").strip()

        if not workflow:
            error_message = "Workflow not initialized"
            async for chunk in self._emit_error_and_result(
                request.session_id,
                error_message,
                request=request,
                stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                trace_id=trace_id,
            ):
                yield chunk
            return

        request.metadata["session_id"] = request.session_id
        if request.user_id and not str(request.metadata.get("user_id", "")).strip():
            request.metadata["user_id"] = request.user_id
        if getattr(request, "user_role", None) and not str(request.metadata.get("user_role", "")).strip():
            request.metadata["user_role"] = request.user_role
        if "user_id" not in request.metadata and str(request.metadata.get("userId", "")).strip():
            request.metadata["user_id"] = request.metadata.get("userId")
        idempotency_key = self._request_idempotency_key(request)
        if idempotency_key:
            replay_started_at = time.perf_counter()
            request.metadata["idempotency_key"] = idempotency_key
            cached_idempotent = await self._load_idempotent_response(request)
            self._mark_stage(stage_timings, "idempotency_lookup", replay_started_at)
            if isinstance(cached_idempotent, dict):
                has_pending_in_payload = "pending_select" in cached_idempotent
                cached_pending = self._merge_pending_select(
                    cached_idempotent.get("pending_select"),
                    cached_idempotent.get("workflow"),
                )
                if cached_pending is None:
                    cached_pending = self._infer_pending_select_from_sql_payload(cached_idempotent.get("sql"))
                if has_pending_in_payload or cached_pending is not None:
                    await self._persist_pending_select_state(request.session_id, cached_pending)
                cached_message = str(cached_idempotent.get("message", "") or "")
                self._mark_stage(stage_timings, "idempotency_replay", replay_started_at)
                if cached_message:
                    yield self._token_line(cached_message)
                else:
                    yield self._token_line("Reused idempotent response.")
                replay_payload = self._hydrate_response_metadata(cached_idempotent)
                replay_payload["trace_id"] = str(replay_payload.get("trace_id") or trace_id)
                replay_payload["stage_timings_ms"] = self._stage_timings_payload(stage_timings, stream_started_at)
                self.metrics.record_idempotency_replay()
                self._record_chat_terminal_metrics(
                    status=str(replay_payload.get("status", "ok")),
                    stage_timings=replay_payload.get("stage_timings_ms"),
                    source="idempotent_replay",
                    error_message=str(replay_payload.get("message", "")),
                )
                yield self._json_line(replay_payload)
                return

        state_load_started_at = time.perf_counter()
        history_payload = await self._load_history(request.session_id)
        flow_state = await self._load_flow_state(request.session_id)
        pending_select_state = await self._load_pending_select_state(request.session_id)
        last_select_state = await self._load_last_select_state(request.session_id)
        self._mark_stage(stage_timings, "state_load", state_load_started_at)
        recent_conversation = self._recent_conversation_window(history_payload, max_turns=5)
        if recent_conversation:
            request.metadata["_recent_conversation"] = recent_conversation
            request.metadata["_recent_conversation_text"] = self._recent_conversation_text(recent_conversation)
        else:
            request.metadata.pop("_recent_conversation", None)
            request.metadata.pop("_recent_conversation_text", None)
        if isinstance(pending_select_state, dict):
            pending_table = str(pending_select_state.get("table", "")).strip()
            if pending_table:
                request.metadata["pending_select_table"] = pending_table
            pending_negation = pending_select_state.get("negation")
            if isinstance(pending_negation, dict):
                request.metadata["pending_select_negation"] = dict(pending_negation)

        load_more_limit, load_more_offset = self._parse_load_more_request(str(request.message or ""))
        if (
            self._is_summary_request(str(request.message or ""))
            and last_select_state
            and isinstance(last_select_state.get("sql"), str)
            and str(last_select_state.get("sql", "")).strip().upper().startswith("SELECT")
        ):
            summary_started_at = time.perf_counter()
            summary_message, summary_sql, summary_sql_data = self._summarize_last_select(
                str(last_select_state.get("sql", "")),
                dict(request.metadata or {}),
            )
            self._mark_stage(stage_timings, "summary_query", summary_started_at)
            if summary_message and summary_sql and summary_sql_data:
                await self._save_last_select_state(
                    request.session_id,
                    {
                        "sql": str(last_select_state.get("sql", "")),
                        "offset": 0,
                        "limit": self._bounded_page_limit(None),
                    },
                )
                async for chunk in self._emit_token_and_result(
                    request,
                    summary_message,
                    status="ok",
                    workflow_payload=None,
                    sql_data=summary_sql_data,
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return

        if (
            load_more_limit is not None
            and last_select_state
            and isinstance(last_select_state.get("sql"), str)
            and str(last_select_state.get("sql", "")).strip().upper().startswith("SELECT")
        ):
            base_sql = str(last_select_state.get("sql", "")).strip()
            loaded_rows, loaded_count, total_records = self._normalize_last_select_progress(last_select_state)
            effective_limit = self._bounded_page_limit(load_more_limit)
            requested_offset = self._safe_int(load_more_offset, -1)
            target_offset = max(loaded_count, requested_offset) if requested_offset >= 0 else loaded_count
            if total_records > 0 and target_offset >= total_records:
                token_msg = f"All records are already loaded ({total_records}/{total_records})."
                await self._save_last_select_state(
                    request.session_id,
                    {
                        "sql": base_sql,
                        "offset": target_offset,
                        "limit": effective_limit,
                        "total_records": total_records,
                        "loaded_count": loaded_count,
                        "loaded_rows": loaded_rows,
                    },
                )
                async for chunk in self._emit_token_and_result(
                    request,
                    token_msg,
                    status="ok",
                    workflow_payload=None,
                    sql_data={
                        "ran": True,
                        "cached": False,
                        "query": self._apply_limit_offset(base_sql, effective_limit, target_offset),
                        "row_count": total_records,
                        "rows_preview": loaded_rows,
                        "total_records": total_records,
                    },
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return
            if total_records > 0:
                remaining = max(0, total_records - target_offset)
                effective_limit = min(effective_limit, remaining if remaining > 0 else effective_limit)
            if effective_limit <= 0:
                effective_limit = 1
            paged_sql = self._apply_limit_offset(base_sql, effective_limit, target_offset)
            load_more_started_at = time.perf_counter()
            try:
                sql_result = await self._run_with_timeout(
                    "SQL execution",
                    self.flow_engine.sql_executor.run({"sql_query": paged_sql, "metadata": request.metadata}),
                    self.sql_timeout_seconds,
                )
                self._mark_stage(stage_timings, "load_more_sql", load_more_started_at)
            except Exception as exc:
                self._mark_stage(stage_timings, "load_more_sql", load_more_started_at)
                async for chunk in self._emit_error_and_result(
                    request.session_id,
                    str(exc),
                    request=request,
                    workflow_payload=None,
                    sql_data={
                        "ran": True,
                        "cached": False,
                        "query": paged_sql,
                        "row_count": 0,
                        "rows_preview": [],
                    },
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return
            if sql_result.get("error"):
                error_message = str(sql_result.get("error"))
                async for chunk in self._emit_error_and_result(
                    request.session_id,
                    error_message,
                    request=request,
                    workflow_payload=None,
                    sql_data={
                        "ran": True,
                        "cached": False,
                        "query": paged_sql,
                        "row_count": 0,
                        "rows_preview": [],
                    },
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return
            page_rows = self._rows_from_sql_result_payload(sql_result)
            if not page_rows:
                page_rows = self._normalize_rows_payload(sql_result.get("rows_preview"))
            if page_rows:
                loaded_rows.extend(page_rows)
            if total_records > 0 and len(loaded_rows) > total_records:
                loaded_rows = loaded_rows[:total_records]
            loaded_count = len(loaded_rows)
            resolved_total = total_records if total_records > 0 else max(loaded_count, self._safe_int(last_select_state.get("row_count"), 0))
            if resolved_total > 0 and loaded_count > resolved_total:
                loaded_count = resolved_total

            if loaded_count <= target_offset:
                token_msg = "No more records found."
            elif resolved_total > 0:
                token_msg = f"Showing {loaded_count} of {resolved_total} record(s)."
            else:
                token_msg = f"Showing {loaded_count} record(s)."
            await self._save_last_select_state(
                request.session_id,
                {
                    "sql": base_sql,
                    "offset": loaded_count,
                    "limit": effective_limit,
                    "total_records": resolved_total,
                    "loaded_count": loaded_count,
                    "loaded_rows": loaded_rows,
                },
            )
            async for chunk in self._emit_token_and_result(
                request,
                token_msg,
                status="ok",
                workflow_payload=None,
                sql_data={
                    "ran": True,
                    "cached": False,
                    "query": paged_sql,
                    "row_count": resolved_total if resolved_total > 0 else loaded_count,
                    "rows_preview": loaded_rows,
                    "total_records": resolved_total if resolved_total > 0 else None,
                },
                stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                trace_id=trace_id,
            ):
                yield chunk
            return

        if pending_select_state and flow_state is None:
            followup = str(request.message or "").strip()
            if followup.lower() in {"cancel", "stop", "exit", "abort"}:
                await self._clear_pending_select_state(request.session_id)
                cancel_msg = "Filter selection cancelled. You can start a new query anytime."
                async for chunk in self._emit_token_and_result(
                    request,
                    cancel_msg,
                    status="ok",
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return
            else:
                table = str(pending_select_state.get("table", "")).strip()
                has_negation_context = isinstance(pending_select_state.get("negation"), dict)
                if table and not has_negation_context and not self._is_likely_conversational_followup(followup):
                    base_filters = dict(pending_select_state.get("filters") or {})
                    followup_pairs = self.kv_parser(followup)
                    merged_filters: Dict[str, Any] = {}
                    for k, v in base_filters.items():
                        key = str(k or "").strip()
                        val = str(v or "").strip()
                        if not key or not val:
                            continue
                        if val.lower() in {"null", "none", "undefined", "all", "any"}:
                            continue
                        merged_filters[key] = val
                    for k, v in followup_pairs.items():
                        key = str(k or "").strip()
                        val = str(v or "").strip()
                        if not key or not val:
                            continue
                        if val.lower() in {"null", "none", "undefined", "all", "any"}:
                            continue
                        merged_filters[key] = val

                    if followup.lower() == "back":
                        request.message = f"show {table}".strip()
                    else:
                        if merged_filters:
                            merged_text = ", ".join(f"{k}={v}" for k, v in merged_filters.items())
                            request.message = f"show {table} {merged_text}".strip()
                        else:
                            # Force follow-up filter input like "today" down SQL path.
                            request.message = f"show {table} {followup}".strip()

        if flow_state:
            if flow_state.get("active_flow"):
                if self._should_interrupt_active_flow(str(request.message or "")):
                    await self._clear_flow_state(request.session_id)
                    flow_state = None
                else:
                    # Route follow-up user input to FlowEngine when a YAML flow is active.
                    active_flow_started_at = time.perf_counter()
                    active_flow_result = await self._handle_active_flow(request, flow_state)
                    resolved_user_input = str(active_flow_result.pop("_display_user_input", "")).strip()
                    if resolved_user_input:
                        request.message = resolved_user_input
                    self._mark_stage(stage_timings, "active_flow", active_flow_started_at)
                    async for chunk in self._emit_token_and_result(
                        request,
                        str(active_flow_result.get("message", "")),
                        final_response=active_flow_result,
                        status=str(active_flow_result.get("status", "ok")),
                        workflow_payload=active_flow_result.get("workflow"),
                        sql_data=active_flow_result.get("sql"),
                        token_usage=active_flow_result.get("token_usage"),
                        stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                        trace_id=trace_id,
                    ):
                        yield chunk
                    return
            await self._clear_flow_state(request.session_id)
            flow_state = None

        if flow_state is None:
            # Optional pre-graph flow path for declarative domain flows.
            flow_start_started_at = time.perf_counter()
            flow_start_response = await self._maybe_start_yaml_flow(request)
            self._mark_stage(stage_timings, "flow_start", flow_start_started_at)
            if flow_start_response is not None:
                async for chunk in self._emit_token_and_result(
                    request,
                    str(flow_start_response.get("message", "")),
                    final_response=flow_start_response,
                    status=str(flow_start_response.get("status", "ok")),
                    workflow_payload=flow_start_response.get("workflow"),
                    sql_data=flow_start_response.get("sql"),
                    token_usage=flow_start_response.get("token_usage"),
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                ):
                    yield chunk
                return

        use_cache = flow_state is None
        cache_key = self._chat_cache_key(request, history_payload)
        if use_cache:
            cache_lookup_started_at = time.perf_counter()
            cached_response = await self._cache_get(cache_key, "chat_response")
            self._mark_stage(stage_timings, "cache_lookup", cache_lookup_started_at)
            if isinstance(cached_response, dict):
                logger.info("Cache HIT for key: %s", cache_key)
                if cached_response.get("sql"):
                    cached_response["sql"]["cached"] = True
                has_pending_in_payload = "pending_select" in cached_response
                cached_pending = self._merge_pending_select(
                    cached_response.get("pending_select"),
                    cached_response.get("workflow"),
                )
                if cached_pending is None:
                    cached_pending = self._infer_pending_select_from_sql_payload(cached_response.get("sql"))
                if has_pending_in_payload or cached_pending is not None:
                    await self._persist_pending_select_state(request.session_id, cached_pending)

                cached_message = cached_response.get("message")
                async for chunk in self._emit_token_and_result(
                    request,
                    str(cached_message or ""),
                    final_response=cached_response,
                    status=str(cached_response.get("status", "ok")),
                    workflow_payload=cached_response.get("workflow"),
                    sql_data=cached_response.get("sql"),
                    token_usage=cached_response.get("token_usage"),
                    stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                    trace_id=trace_id,
                    fallback_token="I processed your previous request from cache.",
                ):
                    yield chunk
                return

        logger.info("Cache MISS for key: %s", cache_key)

        try:
            prior_messages: List[Any] = []
            for item in history_payload:
                role = item.get("role")
                content = str(item.get("content", ""))
                if not content:
                    continue
                if role == "assistant":
                    prior_messages.append(AIMessage(content=content))
                else:
                    prior_messages.append(HumanMessage(content=content))

            logger.info(
                "Invoking workflow with session_id=%s metadata=%s",
                request.session_id,
                self._loggable_metadata(request.metadata),
            )
            inputs = {
                "messages": prior_messages + [HumanMessage(content=request.message)],
                "metadata": request.metadata,
                "retry_count": 0,
            }
            workflow_started_at = time.perf_counter()
            result = await self._run_with_timeout(
                "Workflow execution",
                workflow.ainvoke(inputs),
                self.workflow_timeout_seconds,
            )
            self._mark_stage(stage_timings, "workflow_execution", workflow_started_at)

            final_message = result["messages"][-1].content or ""
            executed_sql = result.get("sql_query", "")
            error = result.get("error", None)
            workflow_payload = result.get("workflow_payload", None)
            report_payload = result.get("report_result", None)
            routed_to_report = str(result.get("route", "") or "").strip().upper() == "REPORT"
            pending_select = self._merge_pending_select(result.get("pending_select"), workflow_payload)
            await self._persist_pending_select_state(request.session_id, pending_select)


            status_code = "error" if error else "ok"
            sql_data = None
            if executed_sql and executed_sql != "SKIP":
                raw_rows_preview = self._normalize_rows_payload(result.get("rows_preview"))
                raw_row_count = self._safe_int(result.get("row_count"), 0)
                raw_total_records = self._safe_int(result.get("total_records"), 0)
                resolved_total_records = max(raw_row_count, raw_total_records)
                sql_data = {
                    "ran": True,
                    "cached": result.get("from_cache", False),
                    "query": executed_sql,
                    "row_count": raw_row_count,
                    "rows_preview": raw_rows_preview,
                }
                if resolved_total_records > 0:
                    sql_data["total_records"] = resolved_total_records
                if str(executed_sql).strip().upper().startswith("SELECT"):
                    await self._save_last_select_state(
                        request.session_id,
                        {
                            "sql": executed_sql,
                            "offset": len(raw_rows_preview),
                            "limit": self._bounded_page_limit(None),
                            "row_count": raw_row_count,
                            "total_records": resolved_total_records,
                            "loaded_count": len(raw_rows_preview),
                            "loaded_rows": raw_rows_preview,
                        },
                    )
                else:
                    await self._clear_last_select_state(request.session_id)

            if status_code == "ok" and use_cache and not workflow_payload and not routed_to_report:
                await self._cache_set(
                    cache_key,
                    self._build_final_response(
                        request.session_id,
                        str(final_message),
                        status=status_code,
                        workflow_payload=workflow_payload,
                        sql_data=sql_data,
                        report_data=report_payload,
                        token_usage=result.get("token_usage", None),
                        pending_select=pending_select,
                        trace_id=trace_id,
                    ),
                    ttl=3600,
                    purpose="chat_response",
                )

            async for chunk in self._emit_token_and_result(
                request,
                str(final_message),
                status=status_code,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                report_data=report_payload,
                token_usage=result.get("token_usage", None),
                pending_select=pending_select,
                stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                trace_id=trace_id,
            ):
                yield chunk

        except Exception as exc:
            logger.error("Workflow execution failed: %s", exc)
            error_message = str(exc)
            async for chunk in self._emit_error_and_result(
                request.session_id,
                error_message,
                request=request,
                stage_timings=self._stage_timings_payload(stage_timings, stream_started_at),
                trace_id=trace_id,
            ):
                yield chunk
