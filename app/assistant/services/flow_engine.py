from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.assistant.nodes.sql_execute_node import SQLExecuteNode
from app.assistant.services.flow_registry import FlowRegistry
from app.assistant.services.sql_builder_service import SQLBuilderService
from app.config import get_settings
from app.services.schema_service import SchemaService

settings = get_settings()


ResolverFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], List[Dict[str, str]]]
ValidatorFn = Callable[[str, Dict[str, Any], Dict[str, Any], Dict[str, Any]], Tuple[bool, str]]
ActionFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class FlowResult:
    message: str
    status: str = "ok"
    workflow: Optional[Dict[str, Any]] = None
    completed: bool = False
    clear_state: bool = False
    sql_data: Optional[Dict[str, Any]] = None


class FlowEngine:
    """YAML-driven guided mutation flow engine."""

    def __init__(self, registry: FlowRegistry):
        self.registry = registry
        self.schema = SchemaService()
        self.builder = SQLBuilderService()
        self.sql_executor = SQLExecuteNode()

        self.resolvers: Dict[str, ResolverFn] = {
            "schedule.list_scheduler_refs": self._resolve_scheduler_refs,
            "schedule.list_facilities": self._resolve_facilities,
            "schedule.list_assets": self._resolve_assets,
            "schedule.list_users": self._resolve_users,
            "schedule.list_tasks": self._resolve_tasks,
        }
        self.validators: Dict[str, ValidatorFn] = {
            "required": self._validate_required,
            "numeric": self._validate_numeric,
            "priority": self._validate_priority,
        }
        self.actions: Dict[str, ActionFn] = {
            "schedule.create_scheduler_task_details": self._action_create_scheduler_task,
        }

    async def run(
        self,
        flow_id: str,
        session_state: Dict[str, Any],
        user_input: str,
        metadata: Dict[str, Any],
    ) -> FlowResult:
        flow = self.registry.get(flow_id)
        states = dict(flow.get("states") or {})
        if not states:
            return FlowResult(message="Flow configuration is invalid.", status="error", clear_state=True)

        if session_state.get("active_flow") != flow_id:
            session_state["active_flow"] = flow_id

        flow_context = dict(session_state.get("flow_context") or {})
        flow_context.setdefault("values", {})
        flow_context.setdefault("history", [])
        flow_context["metadata"] = dict(metadata or {})
        session_state["flow_context"] = flow_context

        if not str(session_state.get("current_state", "")).strip():
            session_state["current_state"] = str(flow.get("start", "start"))

        text_value = str(user_input or "").strip()
        lower_input = text_value.lower()

        if lower_input in {"cancel", "stop", "exit", "abort"}:
            return FlowResult(message="Flow cancelled.", completed=True, clear_state=True)

        if lower_input == "back":
            if not self._go_back(session_state):
                return await self._render_current(flow, session_state, metadata, "Already at the first step.")
            return await self._render_current(flow, session_state, metadata)

        for _ in range(10):
            state_name = str(session_state.get("current_state", "")).strip()
            state_def = states.get(state_name)
            if not isinstance(state_def, dict):
                return FlowResult(message=f"Invalid state: {state_name}", status="error", clear_state=True)

            state_type = str(state_def.get("type", "input")).strip().lower()

            if not self._state_enabled(state_def, session_state):
                next_state = self._resolve_next(state_def, session_state)
                if not next_state:
                    return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
                self._transition(session_state, next_state)
                continue

            if state_type == "system":
                next_state = self._resolve_next(state_def, session_state)
                if not next_state:
                    return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
                self._transition(session_state, next_state)
                continue

            if state_type == "menu":
                result = self._handle_menu(flow, state_def, session_state, text_value, metadata)
                if not result.message and not result.completed and not result.clear_state:
                    text_value = ""
                    continue
                return result

            if state_type == "input":
                result = self._handle_input(flow, state_def, session_state, text_value)
                if not result.message and not result.completed and not result.clear_state:
                    text_value = ""
                    continue
                return result

            if state_type == "confirmation":
                if not text_value:
                    return self._render_confirmation(flow, state_def, session_state)
                if lower_input in {"yes", "y", "confirm", "proceed"}:
                    next_state = self._resolve_next(state_def, session_state)
                    if not next_state:
                        return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
                    self._transition(session_state, next_state)
                    text_value = ""
                    continue
                if lower_input in {"no", "n", "edit"}:
                    no_next = str(state_def.get("on_no", "")).strip()
                    if no_next:
                        self._transition(session_state, no_next)
                        return await self._render_current(flow, session_state, metadata)
                    return FlowResult(message="Type `back` to modify fields, or `yes` to confirm.")
                return self._render_confirmation(flow, state_def, session_state, error="Please reply with yes/no/back/cancel.")

            if state_type == "db_write":
                action_name = str(state_def.get("action", "")).strip()
                action = self.actions.get(action_name)
                if not action:
                    return FlowResult(message=f"Missing action: {action_name}", status="error", clear_state=True)

                action_result = await action(flow, session_state, metadata)
                if action_result.get("status") != "ok":
                    error_next = str(state_def.get("on_error", "")).strip() or self._previous_state(session_state)
                    if error_next:
                        session_state["current_state"] = error_next
                    return await self._render_current(
                        flow,
                        session_state,
                        metadata,
                        prefix_message=str(action_result.get("message", "Failed to execute action.")),
                        status="error",
                    )

                session_state["flow_context"]["last_action"] = dict(action_result)
                next_state = self._resolve_next(state_def, session_state)
                if not next_state:
                    return FlowResult(
                        message=str(action_result.get("message", "Completed.")),
                        completed=True,
                        clear_state=True,
                        sql_data=action_result.get("sql_data"),
                    )
                self._transition(session_state, next_state)
                text_value = ""
                continue

            if state_type == "end":
                message = str(state_def.get("message", "Completed."))
                if "{last_message}" in message:
                    last_message = str((session_state.get("flow_context") or {}).get("last_action", {}).get("message", ""))
                    message = message.replace("{last_message}", last_message)
                sql_data = dict((session_state.get("flow_context") or {}).get("last_action", {}).get("sql_data") or {}) or None
                return FlowResult(message=message, completed=True, clear_state=True, sql_data=sql_data)

            return FlowResult(message=f"Unsupported state type: {state_type}", status="error", clear_state=True)

        return FlowResult(message="Flow exceeded processing limit.", status="error", clear_state=True)

    async def _render_current(
        self,
        flow: Dict[str, Any],
        session_state: Dict[str, Any],
        metadata: Dict[str, Any],
        prefix_message: str = "",
        status: str = "ok",
    ) -> FlowResult:
        states = dict(flow.get("states") or {})
        current = str(session_state.get("current_state", "")).strip()
        state_def = states.get(current) or {}
        state_type = str(state_def.get("type", "input")).strip().lower()

        if state_type == "menu":
            result = self._handle_menu(flow, state_def, session_state, "", metadata)
        elif state_type == "confirmation":
            result = self._render_confirmation(flow, state_def, session_state)
        elif state_type == "input":
            result = self._handle_input(flow, state_def, session_state, "")
        else:
            result = FlowResult(message="Please continue.")

        if prefix_message:
            result.message = f"{prefix_message}\n\n{result.message}" if result.message else prefix_message
        result.status = status if status in {"ok", "error"} else "ok"
        return result

    def _handle_menu(
        self,
        flow: Dict[str, Any],
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        user_input: str,
        metadata: Dict[str, Any],
    ) -> FlowResult:
        options = self._menu_options(state_def, session_state, metadata)
        prompt = str(state_def.get("prompt", "Choose one option."))

        if not options:
            return FlowResult(message=f"{prompt}\nNo options available right now.", status="error")

        if not user_input:
            return FlowResult(
                message=self._render_menu_message(prompt, options),
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        value = self._pick_menu_value(user_input, options)
        if not value:
            return FlowResult(
                message=f"Invalid selection.\n\n{self._render_menu_message(prompt, options)}",
                status="error",
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        capture_key = str(state_def.get("capture", "")).strip()
        if capture_key:
            (session_state.get("flow_context") or {}).setdefault("values", {})[capture_key] = value

        next_state = self._resolve_next(state_def, session_state)
        if not next_state:
            return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
        self._transition(session_state, next_state)
        return FlowResult(message="")

    def _handle_input(
        self,
        flow: Dict[str, Any],
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        user_input: str,
    ) -> FlowResult:
        capture_key = str(state_def.get("capture", "")).strip()
        optional = bool(state_def.get("optional", False))
        default_value = state_def.get("default")
        prompt = str(state_def.get("prompt", "Please enter a value."))

        if not user_input:
            if default_value is not None and capture_key:
                text_default = str(default_value)
                (session_state.get("flow_context") or {}).setdefault("values", {})[capture_key] = text_default
                next_state = self._resolve_next(state_def, session_state)
                if next_state:
                    self._transition(session_state, next_state)
                    return FlowResult(message="")
            return FlowResult(
                message=prompt,
                workflow=self._build_workflow_payload(flow, state_def, session_state),
            )

        if optional and user_input.lower() in {"skip", "none"}:
            next_state = self._resolve_next(state_def, session_state)
            if not next_state:
                return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
            self._transition(session_state, next_state)
            return FlowResult(message="")

        if not optional and not user_input.strip():
            return FlowResult(
                message="Value is required.",
                status="error",
                workflow=self._build_workflow_payload(flow, state_def, session_state),
            )

        validator_name = str(state_def.get("validator", "")).strip()
        validator = self.validators.get(validator_name)
        if validator_name and not validator:
            return FlowResult(message=f"Missing validator: {validator_name}", status="error", clear_state=True)
        if validator:
            ok, error_msg = validator(user_input, flow, state_def, session_state)
            if not ok:
                return FlowResult(
                    message=error_msg or "Invalid value.",
                    status="error",
                    workflow=self._build_workflow_payload(flow, state_def, session_state),
                )

        if capture_key:
            (session_state.get("flow_context") or {}).setdefault("values", {})[capture_key] = user_input.strip()

        next_state = self._resolve_next(state_def, session_state)
        if not next_state:
            return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
        self._transition(session_state, next_state)
        return FlowResult(message="")

    def _render_confirmation(
        self,
        flow: Dict[str, Any],
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        error: str = "",
    ) -> FlowResult:
        values = dict((session_state.get("flow_context") or {}).get("values") or {})
        lines = [str(state_def.get("prompt", "Please confirm:"))]
        for key in sorted(values.keys()):
            lines.append(f"- {key}: {values[key]}")
        lines.append("Reply `yes` to execute, `back` to revise, or `cancel`.")

        message = "\n".join(lines)
        if error:
            message = f"{error}\n\n{message}"

        return FlowResult(
            message=message,
            workflow=self._build_workflow_payload(flow, state_def, session_state),
        )

    def _build_workflow_payload(
        self,
        flow: Dict[str, Any],
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        options: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        values = dict((session_state.get("flow_context") or {}).get("values") or {})
        state_type = str(state_def.get("type", "input")).strip().lower()
        capture = str(state_def.get("capture", "")).strip()

        ui: Dict[str, Any] = {"type": state_type}
        if state_type == "menu":
            ui = {
                "type": "menu",
                "title": str(state_def.get("prompt", "Choose one")),
                "options": options or [],
            }
        elif state_type == "input":
            ui = {
                "type": "input",
                "field": {
                    "id": capture,
                    "label": capture or "value",
                    "kind": "text",
                    "description": str(state_def.get("prompt", "")),
                },
            }
        elif state_type == "confirmation":
            ui = {
                "type": "confirmation",
                "actions": ["yes", "back", "cancel"],
            }

        return {
            "workflow_id": str(flow.get("id", "flow")),
            "state": str(session_state.get("current_state", "")),
            "completed": False,
            "mode": state_type,
            "next_field": capture,
            "collected_data": {
                "operation": "insert",
                "table": str(flow.get("target_table", "")),
                "required_fields": [str(x) for x in (flow.get("required_fields") or [])],
                "collected_fields": values,
            },
            "ui": ui,
        }

    def _menu_options(
        self,
        state_def: Dict[str, Any],
        session_state: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        static_options = state_def.get("options")
        if isinstance(static_options, list) and static_options:
            normalized: List[Dict[str, str]] = []
            for item in static_options:
                if isinstance(item, dict):
                    value = str(item.get("value", "")).strip()
                    label = str(item.get("label", value)).strip() or value
                else:
                    value = str(item).strip()
                    label = value
                if value:
                    normalized.append({"value": value, "label": label})
            return normalized

        resolver_name = str(state_def.get("resolver", "")).strip()
        resolver = self.resolvers.get(resolver_name)
        if not resolver:
            return []
        return resolver({}, state_def, session_state)

    @staticmethod
    def _render_menu_message(prompt: str, options: List[Dict[str, str]]) -> str:
        lines = [prompt]
        for idx, option in enumerate(options, start=1):
            lines.append(f"{idx}. {option.get('label', option.get('value', ''))}")
        lines.append("Type option number or value. You can also type `back` or `cancel`.")
        return "\n".join(lines)

    @staticmethod
    def _pick_menu_value(user_input: str, options: List[Dict[str, str]]) -> str:
        text_value = str(user_input or "").strip()
        if not text_value:
            return ""

        if text_value.isdigit():
            idx = int(text_value)
            if 1 <= idx <= len(options):
                return str(options[idx - 1].get("value", "")).strip()

        lower_value = text_value.lower()
        for option in options:
            value = str(option.get("value", "")).strip()
            label = str(option.get("label", "")).strip()
            if lower_value == value.lower() or lower_value == label.lower():
                return value

        return ""

    @staticmethod
    def _resolve_next(state_def: Dict[str, Any], session_state: Dict[str, Any]) -> str:
        next_def = state_def.get("next")
        if isinstance(next_def, str):
            return str(next_def).strip()

        if isinstance(next_def, dict):
            conditions = next_def.get("when") or []
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                expr = str(cond.get("condition", "")).strip()
                target = str(cond.get("state", "")).strip()
                if expr and target and FlowEngine._eval_condition(expr, session_state):
                    return target
            return str(next_def.get("default", "")).strip()

        return ""

    @staticmethod
    def _eval_condition(expr: str, session_state: Dict[str, Any]) -> bool:
        values = dict((session_state.get("flow_context") or {}).get("values") or {})

        if "==" in expr:
            left, right = expr.split("==", 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            if left.startswith("context."):
                key = left.split(".", 1)[1]
                return str(values.get(key, "")).strip().lower() == right.lower()
        if "!=" in expr:
            left, right = expr.split("!=", 1)
            left = left.strip()
            right = right.strip().strip("'\"")
            if left.startswith("context."):
                key = left.split(".", 1)[1]
                return str(values.get(key, "")).strip().lower() != right.lower()

        return False

    @staticmethod
    def _state_enabled(state_def: Dict[str, Any], session_state: Dict[str, Any]) -> bool:
        expr = str(state_def.get("enabled_if", "")).strip()
        if not expr:
            return True
        return FlowEngine._eval_condition(expr, session_state)

    @staticmethod
    def _transition(session_state: Dict[str, Any], next_state: str) -> None:
        current = str(session_state.get("current_state", "")).strip()
        history = (session_state.get("flow_context") or {}).setdefault("history", [])
        if current and (not history or history[-1] != current):
            history.append(current)
        session_state["current_state"] = next_state

    @staticmethod
    def _go_back(session_state: Dict[str, Any]) -> bool:
        history = (session_state.get("flow_context") or {}).setdefault("history", [])
        if not history:
            return False
        session_state["current_state"] = str(history.pop()).strip()
        return bool(session_state.get("current_state"))

    @staticmethod
    def _previous_state(session_state: Dict[str, Any]) -> str:
        history = (session_state.get("flow_context") or {}).get("history") or []
        if not history:
            return ""
        return str(history[-1]).strip()

    def _list_lookup_rows(
        self,
        table: str,
        value_column: str,
        label_columns: List[str],
        metadata: Dict[str, Any],
        limit: int = 10,
    ) -> List[Dict[str, str]]:
        db_url = (metadata or {}).get("db_connection_string") or settings.DATABASE_URL
        table_columns = self.schema.get_table_columns([table], db_url=db_url).get(table, set())

        selected_cols = [c for c in [value_column, *label_columns] if c in table_columns]
        if value_column not in selected_cols:
            return []

        where_parts: List[str] = []
        params: Dict[str, Any] = {"limit": max(1, int(limit))}

        company_id = (metadata or {}).get("company_id")
        if company_id and "company_id" in table_columns:
            where_parts.append("company_id = :company_id")
            params["company_id"] = company_id

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        escaped_table = f"`{table}`" if table == "user" else table
        sql = f"SELECT {', '.join(selected_cols)} FROM {escaped_table}{where_clause} ORDER BY {value_column} DESC LIMIT :limit;"

        engine = self.schema.get_engine_for_url(db_url)
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(text(sql), params).mappings().all()]

        options: List[Dict[str, str]] = []
        for row in rows:
            value = str(row.get(value_column, "")).strip()
            if not value:
                continue
            label_parts = []
            for col in label_columns:
                if col == value_column:
                    continue
                cell = row.get(col)
                if cell is None:
                    continue
                rendered = str(cell).strip()
                if not rendered:
                    continue
                label_parts.append(rendered)
            label = f"{value} - {' | '.join(label_parts[:3])}" if label_parts else value
            options.append({"value": value, "label": label})

        return options

    def _resolve_scheduler_refs(
        self,
        _: Dict[str, Any],
        __: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        return self._list_lookup_rows(
            table="scheduler_details",
            value_column="id",
            label_columns=["date", "occurrence"],
            metadata=metadata,
        )

    def _resolve_facilities(
        self,
        _: Dict[str, Any],
        __: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        return self._list_lookup_rows(
            table="facility",
            value_column="id",
            label_columns=["name", "code"],
            metadata=metadata,
        )

    def _resolve_assets(
        self,
        _: Dict[str, Any],
        __: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        return self._list_lookup_rows(
            table="asset",
            value_column="id",
            label_columns=["name", "code"],
            metadata=metadata,
        )

    def _resolve_users(
        self,
        _: Dict[str, Any],
        __: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        return self._list_lookup_rows(
            table="user",
            value_column="id",
            label_columns=["first_name", "last_name"],
            metadata=metadata,
        )

    def _resolve_tasks(
        self,
        _: Dict[str, Any],
        __: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        metadata = dict((session_state.get("flow_context") or {}).get("metadata") or {})
        return self._list_lookup_rows(
            table="task_description",
            value_column="id",
            label_columns=["name"],
            metadata=metadata,
        )

    @staticmethod
    def _validate_required(
        value: str,
        _: Dict[str, Any],
        __: Dict[str, Any],
        ___: Dict[str, Any],
    ) -> Tuple[bool, str]:
        if str(value).strip():
            return True, ""
        return False, "This value is required."

    @staticmethod
    def _validate_numeric(
        value: str,
        _: Dict[str, Any],
        __: Dict[str, Any],
        ___: Dict[str, Any],
    ) -> Tuple[bool, str]:
        text_value = str(value).strip()
        if text_value.isdigit():
            return True, ""
        return False, "Please enter a numeric value."

    @staticmethod
    def _validate_priority(
        value: str,
        _: Dict[str, Any],
        __: Dict[str, Any],
        ___: Dict[str, Any],
    ) -> Tuple[bool, str]:
        normalized = str(value).strip().lower()
        if normalized in {"high", "medium", "low"}:
            return True, ""
        return False, "Priority must be High, Medium, or Low."

    async def _action_create_scheduler_task(
        self,
        flow: Dict[str, Any],
        session_state: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        values = dict((session_state.get("flow_context") or {}).get("values") or {})
        company_id = (metadata or {}).get("company_id")

        fields = {
            "sche_details_id": values.get("sche_details_id"),
            "task_description_id": values.get("task_description_id"),
            "priority": values.get("priority"),
            "task_est_time": values.get("task_est_time"),
            "scheduled_ref_no": values.get("scheduled_ref_no") or f"AUTO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        }

        if str(values.get("task_for", "")).strip().lower() == "asset":
            fields["asset_id"] = values.get("asset_id_or_name")

        sql, err = self.builder.build_insert(str(flow.get("target_table", "")), fields, company_id)
        if err:
            return {"status": "error", "message": err}

        result = await self.sql_executor.run({"sql_query": sql, "metadata": metadata})
        if result.get("error"):
            return {"status": "error", "message": str(result.get("error"))}

        row_count = int(result.get("row_count") or 0)
        return {
            "status": "ok",
            "message": f"Create schedule task successful. Rows affected: {row_count}.",
            "sql_data": {
                "ran": True,
                "cached": False,
                "query": sql,
                "row_count": row_count,
                "rows_preview": result.get("rows_preview") or [],
            },
        }
