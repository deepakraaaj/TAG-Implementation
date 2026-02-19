from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.assistant.nodes.sql_execute_node import SQLExecuteNode
from app.assistant.services.flow_plugins.manifest_flow_plugin import ManifestFlowPlugin
from app.assistant.services.flow_registry import FlowRegistry
from app.assistant.services.sql_builder_service import SQLBuilderService
from app.services.schema_service import SchemaService


ResolverFn = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any], int, str], List[Dict[str, str]]]
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
    """YAML-driven guided flow engine."""

    def __init__(self, registry: FlowRegistry):
        self.registry = registry
        self.schema = SchemaService()
        self.builder = SQLBuilderService()
        self.sql_executor = SQLExecuteNode()

        self.resolvers: Dict[str, ResolverFn] = {}
        self.validators: Dict[str, ValidatorFn] = {
            "required": self._validate_required,
            "numeric": self._validate_numeric,
            "priority": self._validate_priority,
        }
        self.actions: Dict[str, ActionFn] = {}

        # Generic manifest-driven plugin; flows should remain declarative.
        self._register_plugin(ManifestFlowPlugin(self.schema, self.builder, self.sql_executor))

    def _register_plugin(self, plugin: Any) -> None:
        plugin_resolvers = getattr(plugin, "resolvers", lambda: {})()
        plugin_actions = getattr(plugin, "actions", lambda: {})()
        if isinstance(plugin_resolvers, dict):
            self.resolvers.update(plugin_resolvers)
        if isinstance(plugin_actions, dict):
            self.actions.update(plugin_actions)

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
        state_name = str(session_state.get("current_state", "")).strip()
        flow_context = (session_state.get("flow_context") or {})
        menu_pages = flow_context.setdefault("menu_pages", {})
        menu_options_state = flow_context.setdefault("menu_options", {})
        current_page = max(0, int(menu_pages.get(state_name, 0) or 0))

        active_options = menu_options_state.get(state_name)
        if isinstance(active_options, list) and active_options:
            options = self._dedupe_options(active_options)
        else:
            options = self._menu_options(state_def, session_state, metadata, page=current_page, search_text="")
        prompt = str(state_def.get("prompt", "Choose one option."))

        if not options:
            return FlowResult(message=f"{prompt}\nNo options available right now.", status="error")

        if not user_input:
            return FlowResult(
                message=self._render_menu_message(prompt, options),
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        cmd = str(user_input).strip().lower()
        if cmd in {"more", "next"}:
            menu_options_state.pop(state_name, None)
            menu_pages[state_name] = current_page + 1
            options = self._menu_options(state_def, session_state, metadata, page=current_page + 1, search_text="")
            if not options:
                menu_pages[state_name] = current_page
                options = self._menu_options(state_def, session_state, metadata, page=current_page, search_text="")
            return FlowResult(
                message=self._render_menu_message(prompt, options),
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        if cmd == "prev":
            menu_options_state.pop(state_name, None)
            prev_page = max(0, current_page - 1)
            menu_pages[state_name] = prev_page
            options = self._menu_options(state_def, session_state, metadata, page=prev_page, search_text="")
            return FlowResult(
                message=self._render_menu_message(prompt, options),
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        value = self._pick_menu_value(user_input, options)
        if not value:
            resolver_name = str(state_def.get("resolver", "")).strip()
            # For resolver-driven menus, treat arbitrary text as a DB search term.
            if resolver_name:
                menu_pages[state_name] = 0
                searched_options = self._menu_options(
                    state_def,
                    session_state,
                    metadata,
                    page=0,
                    search_text=str(user_input or "").strip(),
                )
                if searched_options:
                    menu_options_state[state_name] = searched_options
                    if len(searched_options) == 1:
                        only_value = str(searched_options[0].get("value", "")).strip()
                        if only_value:
                            capture_key = str(state_def.get("capture", "")).strip()
                            if capture_key:
                                (session_state.get("flow_context") or {}).setdefault("values", {})[capture_key] = only_value
                            menu_pages[state_name] = 0
                            menu_options_state.pop(state_name, None)
                            next_state = self._resolve_next(state_def, session_state)
                            if not next_state:
                                return FlowResult(message="Flow stopped unexpectedly.", status="error", clear_state=True)
                            self._transition(session_state, next_state)
                            return FlowResult(message="")
                    return FlowResult(
                        message=(
                            "Found matching options from DB:\n\n"
                            + self._render_menu_message(prompt, searched_options)
                        ),
                        workflow=self._build_workflow_payload(flow, state_def, session_state, options=searched_options),
                    )
                return FlowResult(
                    message=(
                        "No matching options found in DB for that text.\n\n"
                        + self._render_menu_message(prompt, options)
                    ),
                    status="error",
                    workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
                )
            return FlowResult(
                message=f"Invalid selection.\n\n{self._render_menu_message(prompt, options)}",
                status="error",
                workflow=self._build_workflow_payload(flow, state_def, session_state, options=options),
            )

        capture_key = str(state_def.get("capture", "")).strip()
        if capture_key:
            (session_state.get("flow_context") or {}).setdefault("values", {})[capture_key] = value
        menu_pages[state_name] = 0
        menu_options_state.pop(state_name, None)

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
        page: int = 0,
        search_text: str = "",
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
        return self._dedupe_options(resolver({}, state_def, session_state, page, search_text))

    @staticmethod
    def _dedupe_options(options: List[Dict[str, str]]) -> List[Dict[str, str]]:
        deduped: List[Dict[str, str]] = []
        seen = set()
        for option in options:
            if not isinstance(option, dict):
                continue
            value = str(option.get("value", "")).strip()
            label = str(option.get("label", value)).strip() or value
            if not value:
                continue
            key = (value, label.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append({"value": value, "label": label})
        return deduped

    @staticmethod
    def _render_menu_message(prompt: str, options: List[Dict[str, str]]) -> str:
        lines = [prompt]
        for idx, option in enumerate(options, start=1):
            lines.append(f"{idx}. {option.get('label', option.get('value', ''))}")
        lines.append(
            "Choose an option number/value, or type text to search the DB. "
            "Use `more` for more options, `prev` for previous, or `back`/`cancel` anytime."
        )
        return "\n".join(lines)

    @staticmethod
    def _pick_menu_value(user_input: str, options: List[Dict[str, str]]) -> str:
        text_value = str(user_input or "").strip()
        if not text_value:
            return ""

        def norm(v: str) -> str:
            return "".join(ch for ch in str(v or "").lower() if ch.isalnum())

        def parse_time_token(v: str) -> tuple[str, str, str] | None:
            # Supports "12pm", "12:00 pm", "12.00PM", and time fragments inside labels.
            m = re.search(r"(\d{1,2})(?:[:.]?(\d{2}))?\s*([ap]m)\b", str(v or "").lower())
            if not m:
                return None
            hh = m.group(1)
            mm = m.group(2) or "00"
            ap = m.group(3)
            return hh, mm, ap

        def canonical_time(v: str) -> str:
            parsed = parse_time_token(v)
            if not parsed:
                return ""
            hh, mm, ap = parsed
            return f"{int(hh):02d}{mm}{ap}"

        if text_value.isdigit():
            idx = int(text_value)
            if 1 <= idx <= len(options):
                return str(options[idx - 1].get("value", "")).strip()

        lower_value = text_value.lower()
        normalized_input = norm(text_value)
        input_time = canonical_time(text_value)
        for option in options:
            value = str(option.get("value", "")).strip()
            label = str(option.get("label", "")).strip()
            normalized_label = norm(label)
            if (
                lower_value == value.lower()
                or lower_value == label.lower()
                or normalized_input == norm(value)
                or normalized_input == normalized_label
            ):
                return value
            # Time equivalence (e.g. "12pm" vs "12.00 PM" vs "12:00pm").
            if input_time and input_time == canonical_time(label):
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
