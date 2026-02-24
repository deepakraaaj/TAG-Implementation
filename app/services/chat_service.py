import json
import logging
import re
import uuid
import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from app.assistant.services.flow_engine import FlowEngine
from app.assistant.services.flow_registry import FlowRegistry
from app.assistant.services.intent_service import IntentService
from app.assistant.services.sql_builder_service import SQLBuilderService
from app.config import get_settings
from app.core import lifespan
from app.domains.registry import DomainRegistry
from app.schemas.chat import ChatRequest
from app.services.cache import cache
from app.services.chat_support.history_store import ChatHistoryStore
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)
settings = get_settings()


class ChatService:
    _DEFAULT_SUMMARY_SPEC: Dict[str, Any] = {
        "entity_label": "tasks",
        "status_column": "status",
        "status_buckets": [
            {"key": "completed", "label": "Completed", "values": ["completed", "2"]},
            {"key": "pending", "label": "Pending", "values": ["pending", "0"]},
            {"key": "in_progress", "label": "In Progress", "values": ["in progress", "1"]},
            {"key": "overdue", "label": "Overdue", "values": ["overdue", "3"]},
        ],
    }

    def __init__(self):
        self.schema = SchemaService()
        self.intent = IntentService()
        self.flow_engine = FlowEngine(FlowRegistry())
        self.history_store = ChatHistoryStore(ttl_seconds=86400, max_messages=100)
        self.flow_mode = str(getattr(settings, "ASSISTANT_FLOW_MODE", "yaml") or "yaml").strip().lower()
        self.workflow_timeout_seconds = max(1, int(getattr(settings, "QUERY_TIMEOUT_SECONDS", 30) or 30))
        self.sql_timeout_seconds = max(1, int(getattr(settings, "QUERY_TIMEOUT_SECONDS", 30) or 30))
        self.default_page_size = 20
        self.max_page_size = max(1, int(getattr(settings, "MAX_PAGE_SIZE", 1000) or 1000))
        if self.flow_mode != "yaml":
            self.flow_mode = "yaml"

    @staticmethod
    def _flow_state_key(session_id: str) -> str:
        return cache.generate_key("flow_state", session_id)

    @staticmethod
    def _pending_select_key(session_id: str) -> str:
        return cache.generate_key("pending_select", session_id)

    @staticmethod
    def _last_select_key(session_id: str) -> str:
        return cache.generate_key("last_select", session_id)

    @staticmethod
    def _idempotency_cache_key(session_id: str, idempotency_key: str) -> str:
        return cache.generate_key("chat_idempotent", session_id, idempotency_key)

    async def _load_history(self, session_id: str) -> List[Dict[str, str]]:
        return await self.history_store.load(session_id)

    async def _append_history_turn(self, session_id: str, user_message: Any, assistant_message: Any) -> None:
        await self.history_store.append_turn(session_id, user_message, assistant_message)

    async def _load_flow_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await cache.get(self._flow_state_key(session_id))
        return state if isinstance(state, dict) else None

    async def _save_flow_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await cache.set(self._flow_state_key(session_id), state, ttl=3600)

    async def _clear_flow_state(self, session_id: str) -> None:
        await cache.delete(self._flow_state_key(session_id))

    async def _load_pending_select_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await cache.get(self._pending_select_key(session_id))
        return state if isinstance(state, dict) else None

    async def _save_pending_select_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await cache.set(self._pending_select_key(session_id), state, ttl=1800)

    async def _clear_pending_select_state(self, session_id: str) -> None:
        await cache.delete(self._pending_select_key(session_id))

    async def _load_last_select_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await cache.get(self._last_select_key(session_id))
        return state if isinstance(state, dict) else None

    async def _save_last_select_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await cache.set(self._last_select_key(session_id), state, ttl=1800)

    async def _clear_last_select_state(self, session_id: str) -> None:
        await cache.delete(self._last_select_key(session_id))

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
    def _is_summary_request(text: str) -> bool:
        msg = str(text or "").strip().lower()
        if not msg:
            return False
        patterns = [
            r"\bsummary\b",
            r"\bsummarize\b",
            r"\bhow many\b.*\bcomplete(d)?\b",
            r"\bcomplete(d)?\b.*\bhow many\b",
        ]
        return any(re.search(p, msg) for p in patterns)

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
            "status_buckets": normalized_buckets,
        }

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
            for bucket in spec.get("status_buckets") or []:
                key = ChatService._safe_identifier(str(bucket.get("key", "")), "")
                label = str(bucket.get("label", "")).strip()
                if not key or not label:
                    continue
                count = int(row.get(f"{key}_count") or 0)
                rows_preview[f"{key}_count"] = count
                metrics.append(f"{label} {count}")
                if entity_label == "tasks":
                    rows_preview[f"{key}_tasks"] = count

            summary_tail = ", ".join(metrics) if metrics else "No status buckets configured."
            message = f"Summary: total {entity_label} {total}. {summary_tail}."

            if entity_label == "tasks":
                rows_preview["total_tasks"] = total

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
    def _pending_select_from_workflow_payload(workflow_payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(workflow_payload, dict):
            return None
        if str(workflow_payload.get("workflow_id", "")).strip() != "select_filters":
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

    def _request_idempotency_key(self, request: ChatRequest) -> str:
        direct = str(getattr(request, "idempotency_key", "") or "").strip()
        if direct:
            return direct
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        return str(metadata.get("idempotency_key", "") or "").strip()

    async def _load_idempotent_response(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        key = self._request_idempotency_key(request)
        if not key:
            return None
        response = await cache.get(self._idempotency_cache_key(request.session_id, key))
        return response if isinstance(response, dict) else None

    async def _store_idempotent_response(self, request: ChatRequest, response_payload: Dict[str, Any]) -> None:
        key = self._request_idempotency_key(request)
        if not key or not isinstance(response_payload, dict):
            return
        await cache.set(self._idempotency_cache_key(request.session_id, key), response_payload, ttl=3600)

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
        token_usage: Optional[Dict[str, Any]] = None,
        trace_id: str = "",
    ) -> str:
        return self._json_line(
            self._build_final_response(
                session_id,
                message,
                status=status,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                token_usage=token_usage,
                trace_id=trace_id,
            )
        )

    async def _emit_token_and_result(
        self,
        request: ChatRequest,
        message: str,
        final_response: Optional[Dict[str, Any]] = None,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        stage_timings: Optional[Dict[str, float]] = None,
        trace_id: str = "",
        fallback_token: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        resolved_trace_id = str(trace_id or (request.metadata or {}).get("trace_id") or "").strip()
        token_message = str(message or "").strip()
        if token_message:
            yield self._token_line(token_message)
        elif fallback_token:
            yield self._token_line(str(fallback_token))
        await self._append_history_turn(request.session_id, request.message, str(message or ""))
        if isinstance(final_response, dict):
            payload = dict(final_response)
        else:
            payload = self._build_final_response(
                request.session_id,
                str(message or ""),
                status=status,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                token_usage=token_usage,
                stage_timings=stage_timings,
                trace_id=resolved_trace_id,
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
            stage_timings=stage_timings,
            trace_id=resolved_trace_id,
        )
        if request is not None:
            await self._store_idempotent_response(request, payload)
        yield self._json_line(payload)

    @staticmethod
    def _build_final_response(
        session_id: str,
        message: str,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        stage_timings: Optional[Dict[str, float]] = None,
        trace_id: str = "",
    ) -> Dict[str, Any]:
        response = {
            "type": "result",
            "session_id": session_id,
            "status": status,
            "labels": [],
            "workflow": workflow_payload,
            "sql": sql_data,
            "token_usage": token_usage,
            "provider_used": "tag_backend",
            "trace_id": str(trace_id or "").strip(),
        }
        if str(message).strip():
            response["message"] = str(message)
        if isinstance(stage_timings, dict) and stage_timings:
            response["stage_timings_ms"] = dict(stage_timings)
        return response

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

    def _yaml_flow_enabled(self) -> bool:
        return self.flow_mode == "yaml"

    async def _maybe_start_yaml_flow(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        if not self._yaml_flow_enabled():
            return None

        if request.metadata is None:
            request.metadata = {}

        message = str(request.message or "").strip()
        if not message:
            return None

        intent = await self.intent.analyze(message)
        operation = str(intent.get("operation", "select")).strip().lower()
        table = self.flow_engine.builder.resolve_table(message, intent)
        if not table:
            return None

        domain = DomainRegistry.get_current_domain()
        if not domain.is_flow_candidate(message, table):
            return None

        flow_id = self._select_flow_binding(self._domain_flow_bindings(domain), table, operation)
        if not flow_id:
            logger.warning("No flow binding found for table `%s` and operation `%s`.", table, operation)
            return None
        if not self.flow_engine.registry.has(flow_id):
            logger.warning("Flow `%s` is not available.", flow_id)
            return None

        initial_fields: Dict[str, Any] = {}
        if isinstance(intent.get("fields"), dict):
            initial_fields.update(intent.get("fields") or {})
        initial_fields.update(SQLBuilderService.parse_kv_pairs(message))

        flow_state = {
            "active_flow": flow_id,
            "current_state": "",
            "flow_context": {
                "operation": "insert",
                "table": table,
                "values": initial_fields,
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

    async def start_session(self):
        return {"session_id": str(uuid.uuid4()), "message": "Session started"}

    async def generate_chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        workflow = lifespan.workflow
        stream_started_at = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        if request.metadata is None:
            request.metadata = {}
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
        if "user_id" not in request.metadata and str(request.metadata.get("userId", "")).strip():
            request.metadata["user_id"] = request.metadata.get("userId")
        idempotency_key = self._request_idempotency_key(request)
        if idempotency_key:
            replay_started_at = time.perf_counter()
            request.metadata["idempotency_key"] = idempotency_key
            cached_idempotent = await self._load_idempotent_response(request)
            self._mark_stage(stage_timings, "idempotency_lookup", replay_started_at)
            if isinstance(cached_idempotent, dict):
                cached_pending = self._merge_pending_select(
                    cached_idempotent.get("pending_select"),
                    cached_idempotent.get("workflow"),
                )
                await self._persist_pending_select_state(request.session_id, cached_pending)
                cached_message = str(cached_idempotent.get("message", "") or "")
                self._mark_stage(stage_timings, "idempotency_replay", replay_started_at)
                if cached_message:
                    yield self._token_line(cached_message)
                else:
                    yield self._token_line("Reused idempotent response.")
                replay_payload = dict(cached_idempotent)
                replay_payload["trace_id"] = str(replay_payload.get("trace_id") or trace_id)
                replay_payload["stage_timings_ms"] = self._stage_timings_payload(stage_timings, stream_started_at)
                yield self._json_line(replay_payload)
                return

        state_load_started_at = time.perf_counter()
        history_payload = await self._load_history(request.session_id)
        flow_state = await self._load_flow_state(request.session_id)
        pending_select_state = await self._load_pending_select_state(request.session_id)
        last_select_state = await self._load_last_select_state(request.session_id)
        self._mark_stage(stage_timings, "state_load", state_load_started_at)
        if isinstance(pending_select_state, dict):
            pending_table = str(pending_select_state.get("table", "")).strip()
            if pending_table:
                request.metadata["pending_select_table"] = pending_table

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
            default_offset = int(last_select_state.get("offset", 0) or 0)
            default_limit = self._bounded_page_limit(int(last_select_state.get("limit", self.default_page_size) or self.default_page_size))
            effective_limit = self._bounded_page_limit(load_more_limit)
            target_offset = int(load_more_offset) if load_more_offset is not None else (default_offset + default_limit)
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
            row_count = int(sql_result.get("row_count") or 0)
            rows_preview = sql_result.get("rows_preview") or []
            token_msg = "No more records found." if row_count == 0 else f"Showing {row_count} more record(s)."
            await self._save_last_select_state(
                request.session_id,
                {"sql": base_sql, "offset": target_offset, "limit": effective_limit},
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
                    "row_count": row_count,
                    "rows_preview": rows_preview,
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
                if table:
                    base_filters = dict(pending_select_state.get("filters") or {})
                    followup_pairs = SQLBuilderService.parse_kv_pairs(followup)
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
                # Route follow-up user input to FlowEngine when a YAML flow is active.
                active_flow_started_at = time.perf_counter()
                active_flow_result = await self._handle_active_flow(request, flow_state)
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
            # Optional pre-graph flow path for scheduler task creation.
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
        cache_key = cache.generate_key("chat", request.session_id, len(history_payload), request.message)
        if use_cache:
            cache_lookup_started_at = time.perf_counter()
            cached_response = await cache.get(cache_key)
            self._mark_stage(stage_timings, "cache_lookup", cache_lookup_started_at)
            if cached_response:
                logger.info("Cache HIT for key: %s", cache_key)
                if cached_response.get("sql"):
                    cached_response["sql"]["cached"] = True
                cached_pending = self._merge_pending_select(
                    cached_response.get("pending_select"),
                    cached_response.get("workflow"),
                )
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

            logger.info("Invoking workflow with session_id: %s, metadata: %s", request.session_id, request.metadata)
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
            pending_select = self._merge_pending_select(result.get("pending_select"), workflow_payload)
            await self._persist_pending_select_state(request.session_id, pending_select)


            status_code = "error" if error else "ok"
            sql_data = None
            if executed_sql and executed_sql != "SKIP":
                sql_data = {
                    "ran": True,
                    "cached": result.get("from_cache", False),
                    "query": executed_sql,
                    "row_count": result.get("row_count"),
                    "rows_preview": result.get("rows_preview"),
                }
                if str(executed_sql).strip().upper().startswith("SELECT"):
                    await self._save_last_select_state(
                        request.session_id,
                        {"sql": executed_sql, "offset": 0, "limit": self._bounded_page_limit(None)},
                    )
                else:
                    await self._clear_last_select_state(request.session_id)

            if status_code == "ok" and use_cache and not workflow_payload:
                await cache.set(
                    cache_key,
                    self._build_final_response(
                        request.session_id,
                        str(final_message),
                        status=status_code,
                        workflow_payload=workflow_payload,
                        sql_data=sql_data,
                        token_usage=result.get("token_usage", None),
                        trace_id=trace_id,
                    ),
                    ttl=3600,
                )

            async for chunk in self._emit_token_and_result(
                request,
                str(final_message),
                status=status_code,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                token_usage=result.get("token_usage", None),
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
