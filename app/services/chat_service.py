import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text

from app.assistant.services.flow_engine import FlowEngine
from app.assistant.services.flow_registry import FlowRegistry
from app.assistant.services.intent_service import IntentService
from app.assistant.services.sql_builder_service import SQLBuilderService
from app.config import get_settings
from app.core import lifespan
from app.schemas.chat import ChatRequest
from app.services.cache import cache
from app.services.chat_support.history_store import ChatHistoryStore
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)
settings = get_settings()


class ChatService:
    def __init__(self):
        self.schema = SchemaService()
        self.intent = IntentService()
        self.flow_engine = FlowEngine(FlowRegistry())
        self.history_store = ChatHistoryStore(ttl_seconds=86400, max_messages=100)
        self.flow_mode = str(getattr(settings, "ASSISTANT_FLOW_MODE", "yaml") or "yaml").strip().lower()
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

    def _summarize_last_select(self, sql: str, metadata: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        base_sql = str(sql or "").strip().rstrip(";")
        if not base_sql:
            return None, None, None

        summary_sql = (
            "SELECT "
            "COUNT(*) AS total_count, "
            "SUM(CASE WHEN LOWER(CAST(status AS CHAR)) IN ('completed','2') THEN 1 ELSE 0 END) AS completed_count, "
            "SUM(CASE WHEN LOWER(CAST(status AS CHAR)) IN ('pending','0') THEN 1 ELSE 0 END) AS pending_count, "
            "SUM(CASE WHEN LOWER(CAST(status AS CHAR)) IN ('in progress','1') THEN 1 ELSE 0 END) AS in_progress_count, "
            "SUM(CASE WHEN LOWER(CAST(status AS CHAR)) IN ('overdue','3') THEN 1 ELSE 0 END) AS overdue_count "
            f"FROM ({base_sql}) summary_rows"
        )

        try:
            db_url = (metadata or {}).get("db_connection_string") or settings.DATABASE_URL
            engine = self.schema.get_engine_for_url(db_url)
            with engine.connect() as conn:
                row = conn.execute(text(summary_sql)).mappings().first() or {}
            total = int(row.get("total_count") or 0)
            completed = int(row.get("completed_count") or 0)
            pending = int(row.get("pending_count") or 0)
            in_progress = int(row.get("in_progress_count") or 0)
            overdue = int(row.get("overdue_count") or 0)
            message = (
                f"Summary: total tasks {total}. "
                f"Completed {completed}, Pending {pending}, In Progress {in_progress}, Overdue {overdue}."
            )
            sql_data = {
                "ran": True,
                "cached": False,
                "query": summary_sql,
                "row_count": 1,
                "rows_preview": [
                    {
                        "total_tasks": total,
                        "completed_tasks": completed,
                        "pending_tasks": pending,
                        "in_progress_tasks": in_progress,
                        "overdue_tasks": overdue,
                    }
                ],
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
    def _build_final_response(
        session_id: str,
        message: str,
        status: str = "ok",
        workflow_payload: Optional[Dict[str, Any]] = None,
        sql_data: Optional[Dict[str, Any]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
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
            "trace_id": "",
        }
        if str(message).strip():
            response["message"] = str(message)
        return response

    @staticmethod
    def _is_flow_candidate(message: str) -> bool:
        text_value = str(message or "").lower()
        return bool(re.search(r"\b(schedule|scheduler|scheduled)\b", text_value))

    def _yaml_flow_enabled(self) -> bool:
        return self.flow_mode == "yaml"

    async def _maybe_start_yaml_flow(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        if not self._yaml_flow_enabled():
            return None

        if request.metadata is None:
            request.metadata = {}

        message = str(request.message or "").strip()
        if not message or not self._is_flow_candidate(message):
            return None

        intent = await self.intent.analyze(message)
        operation = str(intent.get("operation", "select")).strip().lower()
        if operation != "insert":
            return None

        table = self.flow_engine.builder.resolve_table(message, intent)
        if table != "scheduler_task_details":
            return None

        flow_id = "create_schedule"
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

        result = await self.flow_engine.run(flow_id, flow_state, "", dict(request.metadata or {}))
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
            )

        result = await self.flow_engine.run(
            flow_id,
            flow_state,
            str(request.message or ""),
            dict(request.metadata or {}),
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
        )

    async def start_session(self):
        return {"session_id": str(uuid.uuid4()), "message": "Session started"}

    async def generate_chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        workflow = lifespan.workflow

        if not workflow:
            yield json.dumps({"type": "error", "message": "Workflow not initialized"}) + "\n"
            return

        if request.metadata is None:
            request.metadata = {}
        request.metadata["session_id"] = request.session_id
        if request.user_id and not str(request.metadata.get("user_id", "")).strip():
            request.metadata["user_id"] = request.user_id
        if "user_id" not in request.metadata and str(request.metadata.get("userId", "")).strip():
            request.metadata["user_id"] = request.metadata.get("userId")

        history_payload = await self._load_history(request.session_id)
        flow_state = await self._load_flow_state(request.session_id)
        pending_select_state = await self._load_pending_select_state(request.session_id)
        last_select_state = await self._load_last_select_state(request.session_id)
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
            summary_message, summary_sql, summary_sql_data = self._summarize_last_select(
                str(last_select_state.get("sql", "")),
                dict(request.metadata or {}),
            )
            if summary_message and summary_sql and summary_sql_data:
                yield json.dumps({"type": "token", "content": summary_message}) + "\n"
                await self._save_last_select_state(
                    request.session_id,
                    {"sql": str(last_select_state.get("sql", "")), "offset": 0, "limit": 20},
                )
                await self._append_history_turn(request.session_id, request.message, summary_message)
                yield json.dumps(
                    self._build_final_response(
                        request.session_id,
                        summary_message,
                        status="ok",
                        workflow_payload=None,
                        sql_data=summary_sql_data,
                    ),
                    default=str,
                ) + "\n"
                return

        if (
            load_more_limit is not None
            and last_select_state
            and isinstance(last_select_state.get("sql"), str)
            and str(last_select_state.get("sql", "")).strip().upper().startswith("SELECT")
        ):
            base_sql = str(last_select_state.get("sql", "")).strip()
            default_offset = int(last_select_state.get("offset", 0) or 0)
            default_limit = int(last_select_state.get("limit", 20) or 20)
            target_offset = int(load_more_offset) if load_more_offset is not None else (default_offset + default_limit)
            paged_sql = self._apply_limit_offset(base_sql, int(load_more_limit), target_offset)
            sql_result = await self.flow_engine.sql_executor.run({"sql_query": paged_sql, "metadata": request.metadata})
            if sql_result.get("error"):
                yield json.dumps({"type": "error", "message": str(sql_result.get("error"))}) + "\n"
                return
            row_count = int(sql_result.get("row_count") or 0)
            rows_preview = sql_result.get("rows_preview") or []
            token_msg = "No more records found." if row_count == 0 else f"Showing {row_count} more record(s)."
            yield json.dumps({"type": "token", "content": token_msg}) + "\n"
            final_response = self._build_final_response(
                request.session_id,
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
            )
            await self._save_last_select_state(
                request.session_id,
                {"sql": base_sql, "offset": target_offset, "limit": int(load_more_limit)},
            )
            await self._append_history_turn(request.session_id, request.message, token_msg)
            yield json.dumps(final_response, default=str) + "\n"
            return

        if pending_select_state and flow_state is None:
            followup = str(request.message or "").strip()
            if followup.lower() in {"cancel", "stop", "exit", "abort"}:
                await self._clear_pending_select_state(request.session_id)
                cancel_msg = "Filter selection cancelled. You can start a new query anytime."
                yield json.dumps({"type": "token", "content": cancel_msg}) + "\n"
                await self._append_history_turn(request.session_id, request.message, cancel_msg)
                yield json.dumps(
                    self._build_final_response(request.session_id, cancel_msg, status="ok"),
                    default=str,
                ) + "\n"
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
                active_flow_result = await self._handle_active_flow(request, flow_state)
                message = str(active_flow_result.get("message", ""))
                yield json.dumps({"type": "token", "content": message}) + "\n"
                await self._append_history_turn(request.session_id, request.message, message)
                yield json.dumps(active_flow_result, default=str) + "\n"
                return
            await self._clear_flow_state(request.session_id)
            flow_state = None

        if flow_state is None:
            # Optional pre-graph flow path for scheduler task creation.
            flow_start_response = await self._maybe_start_yaml_flow(request)
            if flow_start_response is not None:
                flow_message = str(flow_start_response.get("message", ""))
                yield json.dumps({"type": "token", "content": flow_message}) + "\n"
                await self._append_history_turn(request.session_id, request.message, flow_message)
                yield json.dumps(flow_start_response, default=str) + "\n"
                return

        use_cache = flow_state is None
        cache_key = cache.generate_key("chat", request.session_id, len(history_payload), request.message)
        if use_cache:
            cached_response = await cache.get(cache_key)
            if cached_response:
                logger.info("Cache HIT for key: %s", cache_key)
                if cached_response.get("sql"):
                    cached_response["sql"]["cached"] = True
                cached_pending = cached_response.get("pending_select")
                cached_from_workflow = self._pending_select_from_workflow_payload(cached_response.get("workflow"))
                if isinstance(cached_pending, dict):
                    if not str(cached_pending.get("table", "")).strip() and isinstance(cached_from_workflow, dict):
                        cached_pending["table"] = str(cached_from_workflow.get("table", "")).strip()
                    if (not isinstance(cached_pending.get("filters"), dict) or not cached_pending.get("filters")) and isinstance(cached_from_workflow, dict):
                        cached_pending["filters"] = dict(cached_from_workflow.get("filters") or {})
                else:
                    cached_pending = cached_from_workflow
                if isinstance(cached_pending, dict) and str(cached_pending.get("table", "")).strip():
                    await self._save_pending_select_state(request.session_id, cached_pending)
                else:
                    await self._clear_pending_select_state(request.session_id)

                cached_message = cached_response.get("message")
                if cached_message:
                    yield json.dumps({"type": "token", "content": str(cached_message)}) + "\n"
                else:
                    yield json.dumps({"type": "token", "content": "I processed your previous request from cache."}) + "\n"

                await self._append_history_turn(request.session_id, request.message, str(cached_message or ""))

                yield json.dumps(cached_response, default=str) + "\n"
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
            result = await workflow.ainvoke(inputs)

            final_message = result["messages"][-1].content or ""
            executed_sql = result.get("sql_query", "")
            error = result.get("error", None)
            workflow_payload = result.get("workflow_payload", None)
            pending_select = result.get("pending_select")
            pending_from_workflow = self._pending_select_from_workflow_payload(workflow_payload)
            if isinstance(pending_select, dict):
                if not str(pending_select.get("table", "")).strip() and isinstance(pending_from_workflow, dict):
                    pending_select["table"] = str(pending_from_workflow.get("table", "")).strip()
                if (not isinstance(pending_select.get("filters"), dict) or not pending_select.get("filters")) and isinstance(pending_from_workflow, dict):
                    pending_select["filters"] = dict(pending_from_workflow.get("filters") or {})
            else:
                pending_select = pending_from_workflow
            if isinstance(pending_select, dict) and str(pending_select.get("table", "")).strip():
                await self._save_pending_select_state(request.session_id, pending_select)
            else:
                await self._clear_pending_select_state(request.session_id)

            if str(final_message).strip():
                yield json.dumps({"type": "token", "content": str(final_message)}) + "\n"


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
                    await self._save_last_select_state(request.session_id, {"sql": executed_sql, "offset": 0, "limit": 20})
                else:
                    await self._clear_last_select_state(request.session_id)

            final_response = self._build_final_response(
                request.session_id,
                str(final_message),
                status=status_code,
                workflow_payload=workflow_payload,
                sql_data=sql_data,
                token_usage=result.get("token_usage", None),
            )

            if status_code == "ok" and use_cache and not workflow_payload:
                await cache.set(cache_key, final_response, ttl=3600)

            await self._append_history_turn(request.session_id, request.message, str(final_message))

            yield json.dumps(final_response, default=str) + "\n"

        except Exception as exc:
            logger.error("Workflow execution failed: %s", exc)
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
