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
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)
settings = get_settings()


class ChatService:
    def __init__(self):
        self.schema = SchemaService()
        self.intent = IntentService()
        self.flow_engine = FlowEngine(FlowRegistry())
        self.flow_mode = str(getattr(settings, "ASSISTANT_FLOW_MODE", "mutation") or "mutation").strip().lower()
        if self.flow_mode not in {"mutation", "hybrid", "yaml"}:
            self.flow_mode = "mutation"

    @staticmethod
    def _apply_scheduler_task_for_rules(required_fields: List[str], collected_fields: Dict[str, Any]) -> List[str]:
        fields = [str(x) for x in required_fields]
        task_for = str((collected_fields or {}).get("task_for", "")).strip().lower()

        if task_for == "facility":
            fields = [f for f in fields if f != "asset_id_or_name"]

        return fields

    @staticmethod
    def _history_key(session_id: str) -> str:
        return cache.generate_key("history", session_id)

    @staticmethod
    def _mutation_key(session_id: str) -> str:
        return cache.generate_key("mutation_state", session_id)

    @staticmethod
    def _pending_select_key(session_id: str) -> str:
        return cache.generate_key("pending_select", session_id)

    @staticmethod
    def _last_select_key(session_id: str) -> str:
        return cache.generate_key("last_select", session_id)

    async def _load_history(self, session_id: str) -> List[Dict[str, str]]:
        history = await cache.get(self._history_key(session_id))
        if isinstance(history, list):
            return [h for h in history if isinstance(h, dict) and "role" in h and "content" in h]
        return []

    async def _save_history(self, session_id: str, history: List[Dict[str, str]]) -> None:
        trimmed = history[-20:]
        await cache.set(self._history_key(session_id), trimmed, ttl=86400)

    async def _load_mutation_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = await cache.get(self._mutation_key(session_id))
        return state if isinstance(state, dict) else None

    async def _save_mutation_state(self, session_id: str, state: Dict[str, Any]) -> None:
        await cache.set(self._mutation_key(session_id), state, ttl=3600)

    async def _clear_mutation_state(self, session_id: str) -> None:
        await cache.delete(self._mutation_key(session_id))

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
    def _next_missing_field(required_fields: List[str], collected_fields: Dict[str, Any]) -> str:
        for field in required_fields:
            if not str(collected_fields.get(field, "")).strip():
                return field
        return ""

    @staticmethod
    def _remaining_fields(required_fields: List[str], collected_fields: Dict[str, Any]) -> List[str]:
        return [f for f in required_fields if not str(collected_fields.get(f, "")).strip()]

    @staticmethod
    def _input_kind(field_name: str) -> str:
        name = str(field_name).lower()
        if "date" in name:
            return "date"
        if re.search(r"(^id$|_id$|count|qty|quantity|amount|price|occurrence|number|ref_no)", name):
            return "numeric"
        if name in {"is_active", "active", "enabled"}:
            return "boolean"
        return "text"

    @staticmethod
    def _lookup_field_label(field_name: str) -> str:
        labels = {
            "sche_details_id": "Scheduler",
            "task_for": "Task For",
            "facility_id_or_name": "Facility",
            "asset_id_or_name": "Asset",
            "assigned_user": "User",
            "task_description_id": "Task",
            "priority": "Priority",
            "task_est_time": "Task EST (Mins)",
            "scheduled_ref_no": "Schedule Ref",
        }
        return labels.get(field_name, field_name)

    @staticmethod
    def _suggested_options(field_name: str) -> List[Dict[str, str]]:
        name = str(field_name).lower()
        if name == "task_for":
            return [
                {"label": "Facility", "value": "facility"},
                {"label": "Asset", "value": "asset"},
            ]
        if name == "status":
            return [
                {"label": "Pending", "value": "Pending"},
                {"label": "In Progress", "value": "In Progress"},
                {"label": "Completed", "value": "Completed"},
                {"label": "Overdue", "value": "Overdue"},
            ]
        if name == "facility_status":
            return [
                {"label": "Assigned", "value": "Assigned"},
                {"label": "In Progress", "value": "In Progress"},
                {"label": "Overdue", "value": "Overdue"},
                {"label": "Delay In Progress", "value": "Delay In Progress"},
                {"label": "Completed", "value": "Completed"},
            ]
        if name == "priority":
            return [
                {"label": "High", "value": "High"},
                {"label": "Medium", "value": "Medium"},
                {"label": "Low", "value": "Low"},
            ]
        if name == "occurrence":
            return [
                {"label": "Daily", "value": "1"},
                {"label": "Weekly", "value": "2"},
                {"label": "Monthly", "value": "3"},
                {"label": "Quarterly", "value": "4"},
            ]
        if name in {"is_active", "active", "enabled"}:
            return [
                {"label": "Yes", "value": "1"},
                {"label": "No", "value": "0"},
            ]
        return []

    @staticmethod
    def _lookup_table_config(field_name: str) -> Dict[str, Any]:
        configs = {
            "sche_details_id": {
                "table": "scheduler_details",
                "value_column": "id",
                "display_columns": ["id", "time", "schedule_time", "start_time", "date", "occurrence"],
                "search_columns": ["id"],
                "order_by": "id DESC",
                "title": "Choose a scheduler",
            },
            "facility_id_or_name": {
                "table": "facility",
                "value_column": "id",
                "display_columns": ["id", "name", "code", "is_active"],
                "search_columns": ["id", "name", "code"],
                "order_by": "id DESC",
                "title": "Choose a facility",
            },
            "asset_id_or_name": {
                "table": "asset",
                "value_column": "id",
                "display_columns": ["id", "name", "code", "is_active"],
                "search_columns": ["id", "name", "code"],
                "order_by": "id DESC",
                "title": "Choose an asset",
            },
            "assigned_user": {
                "table": "user",
                "value_column": "id",
                "display_columns": ["id", "first_name", "last_name", "is_active"],
                "search_columns": ["id", "first_name", "last_name"],
                "order_by": "id DESC",
                "title": "Choose a user",
            },
            "task_description_id": {
                "table": "task_description",
                "value_column": "id",
                "display_columns": ["id", "name", "is_active"],
                "search_columns": ["id", "name"],
                "order_by": "id DESC",
                "title": "Choose a task",
            },
        }
        return dict(configs.get(field_name, {}))

    def _fetch_lookup_rows(
        self,
        field_name: str,
        metadata: Dict[str, Any],
        page: int,
        page_size: int,
        search_text: str = "",
    ) -> Tuple[List[Dict[str, Any]], int]:
        cfg = self._lookup_table_config(field_name)
        if not cfg:
            return [], 0

        db_url = (metadata or {}).get("db_connection_string") or settings.DATABASE_URL
        table = str(cfg["table"])
        display_columns = [str(c) for c in cfg["display_columns"]]
        search_columns = [str(c) for c in cfg.get("search_columns", [])]
        order_by = str(cfg.get("order_by", "id DESC"))
        value_column = str(cfg["value_column"])

        table_columns = self.schema.get_table_columns([table], db_url=db_url).get(table, set())
        selected_columns = [c for c in display_columns if c in table_columns]
        if value_column not in selected_columns and value_column in table_columns:
            selected_columns.insert(0, value_column)
        if not selected_columns:
            return [], 0

        where_parts: List[str] = []
        params: Dict[str, Any] = {
            "limit": max(1, int(page_size)),
            "offset": max(0, int(page)) * max(1, int(page_size)),
        }

        company_id = (metadata or {}).get("company_id")
        if company_id and "company_id" in table_columns:
            where_parts.append("company_id = :company_id")
            params["company_id"] = company_id

        q = str(search_text or "").strip()
        if q:
            search_terms: List[str] = []
            for idx, col in enumerate(search_columns):
                if col not in table_columns:
                    continue
                key = f"q{idx}"
                if col == "id" and q.isdigit():
                    search_terms.append(f"id = :{key}")
                    params[key] = int(q)
                else:
                    search_terms.append(f"LOWER(CAST({col} AS CHAR)) LIKE :{key}")
                    params[key] = f"%{q.lower()}%"
            if search_terms:
                where_parts.append("(" + " OR ".join(search_terms) + ")")

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        escaped_table = f"`{table}`" if table == "user" else table
        cols = ", ".join(selected_columns)
        sql_count = f"SELECT COUNT(*) AS total FROM {escaped_table}{where_clause};"
        sql_list = (
            f"SELECT {cols} FROM {escaped_table}{where_clause} "
            f"ORDER BY {order_by} LIMIT :limit OFFSET :offset;"
        )

        engine = self.schema.get_engine_for_url(db_url)
        with engine.connect() as conn:
            total = int(conn.execute(text(sql_count), params).mappings().first().get("total", 0))
            rows = [dict(r) for r in conn.execute(text(sql_list), params).mappings().all()]
        return rows, total

    def _build_lookup_prompt(
        self,
        state: Dict[str, Any],
        field_name: str,
        metadata: Dict[str, Any],
        search_text: str = "",
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        page = max(0, int(state.get("lookup_page", 0) or 0))
        page_size = max(1, int(state.get("page_size", 5) or 5))
        rows, total = self._fetch_lookup_rows(field_name, metadata, page, page_size, search_text=search_text)
        if total > 0 and not rows and page > 0:
            page = max(0, (total - 1) // page_size)
            state["lookup_page"] = page
            rows, total = self._fetch_lookup_rows(field_name, metadata, page, page_size, search_text=search_text)
        cfg = self._lookup_table_config(field_name)
        value_column = str(cfg.get("value_column", "id"))

        options: List[Dict[str, str]] = []
        lines: List[str] = [f"{cfg.get('title', 'Choose one')} ({len(rows)} shown of {total})"]
        for idx, row in enumerate(rows, start=1):
            value = str(row.get(value_column, "")).strip()
            visible = []
            for k, v in row.items():
                if k == value_column:
                    continue
                if v is None:
                    continue
                text_value = str(v).strip()
                if not text_value or text_value.lower() in {"none", "null"}:
                    continue
                visible.append(text_value)
            detail = " | ".join(visible[:3])
            label = f"{value} - {detail}" if detail else value
            options.append({"index": str(idx), "value": value, "label": label})
            lines.append(f"{idx}. {label}")

        if not rows:
            lines.append("No records found for this page. You can still type ID/name manually.")

        lines.append("Type option number, ID/name, `more`, or `prev`.")

        collected_fields = dict(state.get("collected_fields") or {})
        required_fields = self._apply_scheduler_task_for_rules(
            [str(x) for x in state.get("required_fields", [])], collected_fields
        )
        payload = {
            "workflow_id": "mutation_menu",
            "state": str(state.get("state", "collect_mutation")),
            "completed": False,
            "next_field": field_name,
            "mode": "lookup",
            "pagination": {
                "page": page + 1,
                "page_size": page_size,
                "total_pages": max(1, (total + page_size - 1) // page_size),
                "total_records": total,
            },
            "collected_data": {
                "operation": state.get("operation", "insert"),
                "table": str(state.get("table", "")),
                "required_fields": required_fields,
                "collected_fields": collected_fields,
            },
            "ui": {
                "type": "menu",
                "title": cfg.get("title", "Choose one"),
                "options": options,
            },
        }
        lookup_state = {
            "field": field_name,
            "options": options,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
        return "\n".join(lines), payload, lookup_state

    @staticmethod
    def _build_field_menu(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        table = str(state.get("table", "record"))
        collected_fields = dict(state.get("collected_fields") or {})
        required_fields = ChatService._apply_scheduler_task_for_rules(
            [str(x) for x in state.get("required_fields", [])], collected_fields
        )
        descriptions = state.get("field_descriptions") or {}
        remaining = ChatService._remaining_fields(required_fields, collected_fields)

        page_size = int(state.get("page_size", 5) or 5)
        page = max(0, int(state.get("page", 0) or 0))
        total_pages = max(1, (len(remaining) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)

        start = page * page_size
        end = start + page_size
        page_fields = remaining[start:end]

        lines = [f"Select a field to fill for `{table}` ({len(remaining)} remaining):"]
        for idx, field in enumerate(page_fields, start=1):
            desc = str(descriptions.get(field, "")).strip()
            suffix = f" - {desc}" if desc else ""
            lines.append(f"{idx}. {field}{suffix}")

        controls = []
        if total_pages > 1:
            controls.append(f"Page {page + 1}/{total_pages}")
            controls.append("type `next` or `prev` for more options")
        controls.append("type option number to select")

        pending = str(state.get("pending_field", "")).strip()
        if pending:
            controls.append(f"or directly enter value for recommended `{pending}`")

        message = "\n".join(lines + ["", "; ".join(controls)])

        payload = {
            "workflow_id": "mutation_menu",
            "state": str(state.get("state", "collect_mutation")),
            "completed": False,
            "next_field": pending,
            "mode": "field_selection",
            "pagination": {
                "page": page + 1,
                "page_size": page_size,
                "total_pages": total_pages,
            },
            "collected_data": {
                "operation": state.get("operation", "insert"),
                "table": table,
                "required_fields": required_fields,
                "collected_fields": collected_fields,
            },
            "ui": {
                "type": "menu",
                "title": f"Choose next field for {table}",
                "options": [
                    {
                        "index": idx,
                        "id": field,
                        "label": field,
                        "description": str(descriptions.get(field, "")),
                    }
                    for idx, field in enumerate(page_fields, start=1)
                ],
            },
        }
        return message, payload

    @staticmethod
    def _build_value_prompt(state: Dict[str, Any], field_name: str) -> Tuple[str, Dict[str, Any]]:
        descriptions = state.get("field_descriptions") or {}

        label = ChatService._lookup_field_label(field_name)

        desc = str(descriptions.get(field_name, "")).strip()
        detail = f" ({desc})" if desc else ""

        options = ChatService._suggested_options(field_name)
        kind = ChatService._input_kind(field_name)

        lines = [f"Please provide `{label}`{detail}."]
        if options:
            lines.append("Options:")
            for idx, opt in enumerate(options, start=1):
                lines.append(f"{idx}. {opt['label']} ({opt['value']})")
            lines.append("Type option number or enter custom value.")
        elif kind == "date":
            lines.append("Please enter date in `YYYY-MM-DD` format.")
        elif kind == "numeric":
            lines.append("Please enter a numeric value.")
        elif kind == "boolean":
            lines.append("Please enter `1` (true) or `0` (false).")
        else:
            lines.append("Please enter a text value.")

        payload = {
            "workflow_id": "mutation_menu",
            "state": str(state.get("state", "collect_mutation")),
            "completed": False,
            "next_field": field_name,
            "mode": "field_value",
            "collected_data": {
                "operation": state.get("operation", "insert"),
                "table": str(state.get("table", "record")),
                "required_fields": ChatService._apply_scheduler_task_for_rules(
                    [str(x) for x in state.get("required_fields", [])], dict(state.get("collected_fields") or {})
                ),
                "collected_fields": dict(state.get("collected_fields") or {}),
            },
            "ui": {
                "type": "input",
                "field": {
                    "id": field_name,
                    "label": label,
                    "kind": kind,
                    "description": desc,
                    "options": options,
                },
            },
        }
        return "\n".join(lines), payload

    @staticmethod
    def _build_confirmation_prompt(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        operation = str(state.get("operation", "insert")).lower()
        table = str(state.get("table", "record"))
        collected_fields = dict(state.get("collected_fields") or {})

        lines = [f"Please review before {operation} on `{table}`:"]
        for key in sorted(collected_fields.keys()):
            lines.append(f"- {key}: {collected_fields[key]}")
        lines.append("")
        lines.append("Reply `yes` to confirm and execute, or `no` to edit fields.")

        payload = {
            "workflow_id": "mutation_menu",
            "state": str(state.get("state", "confirm_mutation")),
            "completed": False,
            "next_field": "",
            "mode": "confirmation",
            "collected_data": {
                "operation": operation,
                "table": table,
                "required_fields": ChatService._apply_scheduler_task_for_rules(
                    [str(x) for x in state.get("required_fields", [])], collected_fields
                ),
                "collected_fields": collected_fields,
            },
            "ui": {
                "type": "confirmation",
                "title": f"Confirm {operation} on {table}",
                "actions": ["yes", "no"],
            },
        }
        return "\n".join(lines), payload

    @staticmethod
    def _resolve_field_selection(user_text: str, state: Dict[str, Any]) -> Optional[str]:
        collected_fields = dict(state.get("collected_fields") or {})
        required_fields = ChatService._apply_scheduler_task_for_rules(
            [str(x) for x in state.get("required_fields", [])], collected_fields
        )
        remaining = ChatService._remaining_fields(required_fields, collected_fields)

        text = str(user_text or "").strip().lower()
        if not text:
            return None

        normalized_text = text.split(" - ", 1)[0].strip()

        if normalized_text in {f.lower() for f in remaining}:
            for field in remaining:
                if field.lower() == normalized_text:
                    return field
            return None

        if text.isdigit():
            page_size = int(state.get("page_size", 5) or 5)
            page = max(0, int(state.get("page", 0) or 0))
            start = page * page_size
            end = start + page_size
            page_fields = remaining[start:end]
            index = int(text)
            if 1 <= index <= len(page_fields):
                return page_fields[index - 1]
        return None

    @staticmethod
    def _normalize_option_value(pending_field: str, text: str) -> str:
        options = ChatService._suggested_options(pending_field)
        cleaned = (text or "").strip()
        if not options or not cleaned:
            return cleaned

        if cleaned.isdigit():
            index = int(cleaned)
            if 1 <= index <= len(options):
                return str(options[index - 1]["value"])
            return cleaned

        # Support inputs like "Weekly (2)" or "weekly".
        lower = cleaned.lower()
        for opt in options:
            label = str(opt.get("label", "")).strip().lower()
            value = str(opt.get("value", "")).strip()
            if lower == label or lower == f"{label} ({value})":
                return value
        return cleaned

    @staticmethod
    def _is_valid_field_value(field_name: str, value: str) -> bool:
        kind = ChatService._input_kind(field_name)
        text = (value or "").strip()
        if not text:
            return False
        if kind == "date":
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
        if kind == "numeric":
            return bool(re.fullmatch(r"-?\d+(\.\d+)?", text))
        if kind == "boolean":
            return text in {"0", "1", "true", "false", "yes", "no"}
        return True

    @staticmethod
    def _is_command_like_input(text: str) -> bool:
        lowered = (text or "").lower()
        return any(
            token in lowered
            for token in ["create ", "insert ", "add ", "update ", "show ", "list ", "count ", "get ", "find "]
        )

    @staticmethod
    def _parse_user_field_updates(message: str, pending_field: str) -> Dict[str, str]:
        updates = SQLBuilderService.parse_kv_pairs(message)
        if updates:
            return updates

        text = (message or "").strip()
        if text and pending_field:
            if ChatService._is_command_like_input(text):
                return {}
            normalized = ChatService._normalize_option_value(pending_field, text)
            if ChatService._is_valid_field_value(pending_field, normalized):
                return {pending_field: normalized}
            return {}
        return {}

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
    def _extract_invalid_column(error_message: str) -> str:
        match = re.search(r"for column '([^']+)'", str(error_message))
        return str(match.group(1)).strip() if match else ""

    @staticmethod
    def _extract_missing_required_column(error_message: str) -> str:
        match = re.search(r"Field '([^']+)' doesn't have a default value", str(error_message))
        return str(match.group(1)).strip() if match else ""

    @staticmethod
    def _is_flow_candidate(message: str) -> bool:
        text_value = str(message or "").lower()
        return bool(re.search(r"\b(schedule|scheduler|scheduled)\b", text_value))

    def _yaml_flow_enabled(self) -> bool:
        return self.flow_mode in {"hybrid", "yaml"}

    def _legacy_mutation_enabled(self) -> bool:
        return self.flow_mode in {"mutation", "hybrid"}

    async def _maybe_start_yaml_flow(self, request: ChatRequest) -> Optional[Dict[str, Any]]:
        # Additive flow bootstrap: if this is a schedule insert intent, start YAML flow
        # and persist state in the same mutation-state cache used by existing workflows.
        if not self._yaml_flow_enabled():
            return None

        if request.metadata is None:
            request.metadata = {}

        if request.metadata.get("mutation_context"):
            return None

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
            await self._clear_mutation_state(request.session_id)
        else:
            await self._save_mutation_state(request.session_id, flow_state)

        return self._build_final_response(
            request.session_id,
            result.message,
            status=result.status,
            workflow_payload=result.workflow,
            sql_data=result.sql_data,
        )

    async def _handle_active_flow(self, request: ChatRequest, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        # Flow execution path reuses session mutation state keys:
        # active_flow, current_state, and flow_context.
        if request.metadata is None:
            request.metadata = {}

        flow_id = str(flow_state.get("active_flow", "")).strip()
        if not flow_id:
            await self._clear_mutation_state(request.session_id)
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
            await self._clear_mutation_state(request.session_id)
        else:
            await self._save_mutation_state(request.session_id, flow_state)

        return self._build_final_response(
            request.session_id,
            result.message,
            status=result.status,
            workflow_payload=result.workflow,
            sql_data=result.sql_data,
        )

    def _build_pending_field_response(
        self,
        request: ChatRequest,
        mutation_state: Dict[str, Any],
        search_text: str = "",
    ) -> Dict[str, Any]:
        pending_field = str(mutation_state.get("pending_field", "")).strip()
        if pending_field and self._lookup_table_config(pending_field):
            # Reset lookup pagination when moving to a different field.
            last_lookup_field = str(mutation_state.get("lookup_field", "")).strip()
            if last_lookup_field != pending_field:
                mutation_state["lookup_page"] = 0
            mutation_state["lookup_field"] = pending_field
            message, payload, lookup_state = self._build_lookup_prompt(
                mutation_state,
                pending_field,
                dict(request.metadata or {}),
                search_text=search_text,
            )
            mutation_state["lookup"] = lookup_state
            mutation_state["awaiting"] = "field_value"
            return self._build_final_response(request.session_id, message, workflow_payload=payload)

        message, payload = self._build_value_prompt(mutation_state, pending_field)
        mutation_state["awaiting"] = "field_value"
        mutation_state.pop("lookup", None)
        mutation_state.pop("lookup_field", None)
        mutation_state["lookup_page"] = 0
        return self._build_final_response(request.session_id, message, workflow_payload=payload)

    async def _handle_active_mutation(
        self,
        request: ChatRequest,
        mutation_state: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        user_text = str(request.message or "").strip()
        lower_text = user_text.lower()

        if lower_text in {"cancel", "stop", "exit", "abort"}:
            await self._clear_mutation_state(request.session_id)
            return self._build_final_response(
                request.session_id,
                "Mutation workflow cancelled. Ask a new project request when ready.",
            )

        collected_fields = dict(mutation_state.get("collected_fields") or {})
        required_fields = self._apply_scheduler_task_for_rules(
            [str(x) for x in mutation_state.get("required_fields", [])], collected_fields
        )
        mutation_state["required_fields"] = required_fields
        pending_field = str(mutation_state.get("pending_field", "")).strip()
        awaiting = str(mutation_state.get("awaiting", "field_selection")).strip() or "field_selection"
        page = max(0, int(mutation_state.get("page", 0) or 0))
        page_size = max(1, int(mutation_state.get("page_size", 5) or 5))

        remaining = self._remaining_fields(required_fields, collected_fields)
        if not remaining:
            await self._clear_mutation_state(request.session_id)
            if request.metadata is None:
                request.metadata = {}
            request.metadata["mutation_context"] = {
                "operation": mutation_state.get("operation", "insert"),
                "table": mutation_state.get("table", ""),
                "fields": collected_fields,
            }
            return None

        if not pending_field:
            pending_field = remaining[0]

        if awaiting == "field_selection":
            mutation_state["pending_field"] = pending_field
            mutation_state["awaiting"] = "field_value"
            mutation_state["page"] = page
            mutation_state["page_size"] = page_size
            mutation_state["collected_fields"] = collected_fields
            awaiting = "field_value"
            if not user_text or self._is_command_like_input(user_text):
                response = self._build_pending_field_response(request, mutation_state)
                await self._save_mutation_state(request.session_id, mutation_state)
                return response

        if awaiting == "field_value":
            lookup_cfg = self._lookup_table_config(pending_field)
            lookup_state = dict(mutation_state.get("lookup") or {})
            lookup_selected = False
            if lookup_cfg:
                if lower_text in {"more", "next"}:
                    current_page = int(lookup_state.get("page", mutation_state.get("lookup_page", 0)) or 0)
                    mutation_state["lookup_page"] = current_page + 1
                    response = self._build_pending_field_response(request, mutation_state)
                    await self._save_mutation_state(request.session_id, mutation_state)
                    return response
                if lower_text in {"prev", "back"}:
                    current_page = int(lookup_state.get("page", mutation_state.get("lookup_page", 0)) or 0)
                    mutation_state["lookup_page"] = max(0, current_page - 1)
                    response = self._build_pending_field_response(request, mutation_state)
                    await self._save_mutation_state(request.session_id, mutation_state)
                    return response

                selected_value = ""
                options = [dict(x) for x in lookup_state.get("options", []) if isinstance(x, dict)]
                if user_text.isdigit():
                    option_index = int(user_text)
                    if 1 <= option_index <= len(options):
                        selected_value = str(options[option_index - 1].get("value", "")).strip()

                if not selected_value:
                    for opt in options:
                        value = str(opt.get("value", "")).strip().lower()
                        label = str(opt.get("label", "")).strip().lower()
                        if lower_text == value or lower_text in label:
                            selected_value = str(opt.get("value", "")).strip()
                            break

                if not selected_value and user_text and not self._is_command_like_input(user_text):
                    selected_value = user_text.strip()

                if selected_value:
                    collected_fields[pending_field] = selected_value
                    mutation_state["collected_fields"] = collected_fields
                    required_fields = self._apply_scheduler_task_for_rules(required_fields, collected_fields)
                    mutation_state["required_fields"] = required_fields
                    lookup_selected = True
                else:
                    response = self._build_pending_field_response(request, mutation_state, search_text=user_text)
                    await self._save_mutation_state(request.session_id, mutation_state)
                    return response

            updates = self._parse_user_field_updates(user_text, pending_field)
            accepted_updates = 0
            for key, value in updates.items():
                if key in required_fields and str(value).strip():
                    collected_fields[key] = str(value).strip()
                    accepted_updates += 1
            if lookup_selected:
                accepted_updates += 1

            required_fields = self._apply_scheduler_task_for_rules(required_fields, collected_fields)
            mutation_state["required_fields"] = required_fields

            if accepted_updates == 0:
                mutation_state["pending_field"] = pending_field
                mutation_state["page"] = page
                mutation_state["page_size"] = page_size
                mutation_state["collected_fields"] = collected_fields
                response = self._build_pending_field_response(request, mutation_state)
                await self._save_mutation_state(request.session_id, mutation_state)
                return response

            next_field = self._next_missing_field(required_fields, collected_fields)
            mutation_state["collected_fields"] = collected_fields
            mutation_state["pending_field"] = next_field
            mutation_state["page"] = 0
            mutation_state["page_size"] = page_size

            if next_field:
                mutation_state["awaiting"] = "field_value"
                response = self._build_pending_field_response(request, mutation_state)
                await self._save_mutation_state(request.session_id, mutation_state)
                return response

            mutation_state["awaiting"] = "confirmation"
            mutation_state["pending_field"] = ""
            await self._save_mutation_state(request.session_id, mutation_state)
            message, payload = self._build_confirmation_prompt(mutation_state)
            return self._build_final_response(request.session_id, message, workflow_payload=payload)

        if awaiting == "confirmation":
            if lower_text in {"yes", "y", "confirm", "confirmed", "proceed"}:
                await self._clear_mutation_state(request.session_id)
                if request.metadata is None:
                    request.metadata = {}
                request.metadata["mutation_context"] = {
                    "operation": mutation_state.get("operation", "insert"),
                    "table": mutation_state.get("table", ""),
                    "fields": collected_fields,
                }
                return None

            if lower_text in {"no", "n", "edit", "change"}:
                mutation_state["awaiting"] = "field_value"
                mutation_state["pending_field"] = self._next_missing_field(required_fields, {})
                mutation_state["page"] = 0
                mutation_state["page_size"] = page_size
                response = self._build_pending_field_response(request, mutation_state)
                await self._save_mutation_state(request.session_id, mutation_state)
                return response

            message, payload = self._build_confirmation_prompt(mutation_state)
            return self._build_final_response(request.session_id, message, workflow_payload=payload)

        mutation_state["awaiting"] = "field_value"
        response = self._build_pending_field_response(request, mutation_state)
        await self._save_mutation_state(request.session_id, mutation_state)
        return response

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
        mutation_state = await self._load_mutation_state(request.session_id)
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
                history_payload.extend(
                    [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": summary_message},
                    ]
                )
                await self._save_history(request.session_id, history_payload)
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
            history_payload.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": token_msg},
                ]
            )
            await self._save_history(request.session_id, history_payload)
            yield json.dumps(final_response, default=str) + "\n"
            return

        if pending_select_state and mutation_state is None and "mutation_context" not in request.metadata:
            followup = str(request.message or "").strip()
            if followup.lower() in {"cancel", "stop", "exit", "abort"}:
                await self._clear_pending_select_state(request.session_id)
                cancel_msg = "Filter selection cancelled. You can start a new query anytime."
                yield json.dumps({"type": "token", "content": cancel_msg}) + "\n"
                history_payload.extend(
                    [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": cancel_msg},
                    ]
                )
                await self._save_history(request.session_id, history_payload)
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

        if mutation_state:
            if mutation_state.get("active_flow"):
                # Route follow-up user input to FlowEngine when a YAML flow is active.
                active_flow_result = await self._handle_active_flow(request, mutation_state)
                message = str(active_flow_result.get("message", ""))
                yield json.dumps({"type": "token", "content": message}) + "\n"
                history_payload.extend(
                    [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": message},
                    ]
                )
                await self._save_history(request.session_id, history_payload)
                yield json.dumps(active_flow_result, default=str) + "\n"
                return

            if self._legacy_mutation_enabled():
                active_mutation_result = await self._handle_active_mutation(request, mutation_state)
                if active_mutation_result is not None:
                    message = str(active_mutation_result.get("message", ""))
                    yield json.dumps({"type": "token", "content": message}) + "\n"
                    history_payload.extend(
                        [
                            {"role": "user", "content": request.message},
                            {"role": "assistant", "content": message},
                        ]
                    )
                    await self._save_history(request.session_id, history_payload)
                    yield json.dumps(active_mutation_result, default=str) + "\n"
                    return
            else:
                await self._clear_mutation_state(request.session_id)
                mutation_state = None

        if mutation_state is None and "mutation_context" not in request.metadata:
            # Optional pre-graph flow path for scheduler task creation.
            flow_start_response = await self._maybe_start_yaml_flow(request)
            if flow_start_response is not None:
                flow_message = str(flow_start_response.get("message", ""))
                yield json.dumps({"type": "token", "content": flow_message}) + "\n"
                history_payload.extend(
                    [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": flow_message},
                    ]
                )
                await self._save_history(request.session_id, history_payload)
                yield json.dumps(flow_start_response, default=str) + "\n"
                return

        use_cache = mutation_state is None and "mutation_context" not in request.metadata
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

                history_payload.extend(
                    [
                        {"role": "user", "content": request.message},
                        {"role": "assistant", "content": str(cached_message or "")},
                    ]
                )
                await self._save_history(request.session_id, history_payload)

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

            mutation_context = request.metadata.get("mutation_context") or {}
            if error and mutation_context:
                invalid_column = self._extract_invalid_column(str(error))
                missing_required_column = self._extract_missing_required_column(str(error))

                if invalid_column or missing_required_column:
                    target_column = invalid_column or missing_required_column
                    mutation_fields = dict(mutation_context.get("fields") or {})
                    if invalid_column:
                        mutation_fields.pop(target_column, None)

                    existing_required = [str(x) for x in (mutation_context.get("fields") or {}).keys()]
                    if target_column not in existing_required:
                        existing_required.append(target_column)

                    recovery_state = {
                        "workflow_id": "mutation_menu",
                        "state": f"collect_{mutation_context.get('operation', 'insert')}_{mutation_context.get('table', '')}",
                        "operation": str(mutation_context.get("operation", "insert")),
                        "table": str(mutation_context.get("table", "")),
                        "required_fields": existing_required,
                        "collected_fields": mutation_fields,
                        "pending_field": target_column,
                        "field_descriptions": {},
                        "awaiting": "field_value",
                        "page": 0,
                        "page_size": 5,
                    }
                    await self._save_mutation_state(request.session_id, recovery_state)
                    pending_response = self._build_pending_field_response(request, recovery_state)
                    await self._save_mutation_state(request.session_id, recovery_state)
                    workflow_payload = pending_response.get("workflow", workflow_payload)
                    final_message = str(pending_response.get("message", final_message))
                    error = None
                    executed_sql = ""

            if self._legacy_mutation_enabled() and workflow_payload and not bool(workflow_payload.get("completed")):
                collected_data = workflow_payload.get("collected_data") or {}
                required_fields = [str(x) for x in collected_data.get("required_fields", [])]
                collected_fields = dict(collected_data.get("collected_fields") or {})
                next_field = str(workflow_payload.get("next_field", "")).strip() or self._next_missing_field(
                    required_fields, collected_fields
                )
                ui_fields = (workflow_payload.get("ui") or {}).get("fields") or []
                field_descriptions = {
                    str(f.get("id")): str(f.get("description", ""))
                    for f in ui_fields
                    if isinstance(f, dict) and str(f.get("id", "")).strip()
                }

                state = {
                    "workflow_id": str(workflow_payload.get("workflow_id", "mutation_menu")),
                    "state": str(workflow_payload.get("state", "collect_mutation")),
                    "operation": str(collected_data.get("operation", "insert")),
                    "table": str(collected_data.get("table", "")),
                    "required_fields": required_fields,
                    "collected_fields": collected_fields,
                    "pending_field": next_field,
                    "field_descriptions": field_descriptions,
                    "awaiting": "field_value",
                    "page": 0,
                    "page_size": 5,
                }
                await self._save_mutation_state(request.session_id, state)
                pending_response = self._build_pending_field_response(request, state)
                await self._save_mutation_state(request.session_id, state)
                final_message = str(pending_response.get("message", final_message))
                workflow_payload = pending_response.get("workflow", workflow_payload)

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

            history_payload.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": str(final_message)},
                ]
            )
            await self._save_history(request.session_id, history_payload)

            yield json.dumps(final_response, default=str) + "\n"

        except Exception as exc:
            logger.error("Workflow execution failed: %s", exc)
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
