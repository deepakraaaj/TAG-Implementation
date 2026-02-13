from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import logging

# Ensure built-in workflow steps are registered.
# from app.workflow.steps import schedule  # noqa: F401 (Will be implemented next)

from app.workflow.engine.dsl import (
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowStateDefinition,
)
from app.workflow.engine.registry import runtime_registry
from app.workflow.engine.store import WorkflowSessionSnapshot, WorkflowSessionStore
from app.workflow.engine.types import MenuResolverResult, WorkflowContext
from app.workflow.engine.ui import (
    WorkflowMenuItem,
    WorkflowPagination,
    WorkflowPayload,
    WorkflowUIModel,
)

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    updates: Dict[str, Any] = field(default_factory=dict)
    ui: Optional[WorkflowUIModel] = None
    message: Optional[str] = None
    await_input: bool = False
    advance: bool = False
    next_state: Optional[str] = None
    menu_cache: Optional[Dict[str, Any]] = None
    completed: bool = False


@dataclass
class WorkflowTurn:
    reply: str
    payload: Optional[WorkflowPayload]
    completed: bool


class BaseStep:
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        raise NotImplementedError


class SystemStep(BaseStep):
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        action = runtime_registry.get_action(state.action)
        if not action:
            logger.warning(
                "System step %s on workflow %s missing action.",
                state.name,
                definition.workflow_id,
            )
            return StepResult(advance=True)
        message = await action(context, state)
        return StepResult(message=message, advance=True)


class MenuStep(BaseStep):
    def __init__(self) -> None:
        self.list_commands = {"list", "show", "menu"}
        self.more_commands = {"more", "next"}
        self.list_prefixes = ("show ", "list ")
        self.count_keywords = ("how many", "count", "total", "number of")
        self.skip_commands = {"skip", "default"}

    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        resolver = runtime_registry.get_resolver(state.resolver)
        if not resolver:
            logger.error("Menu state %s missing resolver.", state.name)
            return StepResult(
                message="No resolver configured for menu.",
                await_input=False,
                advance=True,
            )

        cache_state = (
            context.menu_cache if context.menu_cache.get("state") == state.name else {}
        )
        cached_items = cache_state.get("items") or []
        pagination = cache_state.get("pagination") or {}
        start_index = int(cache_state.get("start_index") or 1)
        search_term = cache_state.get("search_term")
        user_input = (context.user_input or "").strip()
        page_size = int(state.ui.get("page_size") or 5) if state.ui else 5
        current_page = int(pagination.get("page") or 1)
        capture_key = state.capture_key or state.config.get("capture")

        if (
            state.optional
            and not user_input
            and capture_key
            and capture_key in context.collected_data
        ):
            return StepResult(
                advance=True, message=f"{capture_key} already captured.", menu_cache={}
            )

        if user_input and state.optional and user_input.lower() in {"skip", "default"}:
            updates: Dict[str, Any] = {}
            if capture_key and state.config.get("default_to_requester"):
                updates[capture_key] = str(context.user_id)
            return StepResult(
                updates=updates,
                message="Skipped selection."
                if not updates
                else "Using your id for this step.",
                advance=True,
                menu_cache={},
            )

        if user_input and cached_items:
            lower = user_input.lower()
            if self._is_count_request(lower):
                message = await self._count_message(state, context)
                current_ui = self._build_cached_ui(state, cache_state)
                return StepResult(
                    message=message,
                    await_input=True,
                    ui=current_ui,
                    menu_cache=context.menu_cache,
                )
            if lower in self.more_commands and pagination.get("has_more"):
                next_page = current_page + 1
                options = await resolver(context, state, next_page, page_size, search_term)
                return StepResult(
                    ui=self._build_menu_ui(state, options, definition),
                    await_input=True,
                    menu_cache=self._serialize_menu_cache(state, options, search_term),
                    message=self._menu_prompt(state, options, search_term),
                )

            selection = self._match_selection(user_input, cached_items, start_index)
            if selection:
                updates = {}
                if capture_key:
                    updates[capture_key] = selection["id"]
                validator_result = await self._run_validator(
                    context, state, selection["id"]
                )
                if validator_result and not validator_result.valid:
                    return StepResult(
                        message=validator_result.message or "Selection is not valid.",
                        await_input=True,
                        ui=self._build_cached_ui(state, cache_state),
                        menu_cache=context.menu_cache,
                )
                return StepResult(
                    updates=updates,
                    message=f"Captured {capture_key or 'selection'} as {selection['label']}.",
                    advance=True,
                    menu_cache={},
                )

            if not selection and (
                lower in self.list_commands or lower.startswith(self.list_prefixes)
            ):
                user_input = ""
                search_term = None
                current_page = 1
            elif not selection:
                requested_search = self._extract_search_term(user_input)
                if requested_search:
                    search_term = requested_search
                    current_page = 1
                else:
                    return StepResult(
                        message="Please select an option from the menu.",
                        await_input=True,
                        ui=self._build_cached_ui(state, cache_state),
                        menu_cache=context.menu_cache,
                    )

        if user_input and not cached_items:
            requested_search = self._extract_search_term(user_input)
            if requested_search:
                search_term = requested_search
                current_page = 1

        options = await resolver(context, state, current_page, page_size, search_term)
        if search_term and not options.items:
            cached_ui = self._build_cached_ui(state, cache_state) if cache_state else None
            message = f"No results match '{search_term}'. Try another name or type 'list' to reset."
            return StepResult(
                message=message,
                await_input=True,
                ui=cached_ui,
                menu_cache=context.menu_cache if cache_state else {},
            )
        lower_input = user_input.lower() if user_input else ""
        serialized_options = self._serialize_menu_cache(state, options, search_term)
        if user_input and self._is_count_request(lower_input):
            message = await self._count_message(state, context)
            return StepResult(
                ui=self._build_menu_ui(state, options, definition),
                await_input=True,
                menu_cache=serialized_options,
                message=message,
            )
        return StepResult(
            ui=self._build_menu_ui(state, options, definition),
            await_input=True,
            menu_cache=serialized_options,
            message=self._menu_prompt(state, options, search_term),
        )

    async def _run_validator(
        self,
        context: WorkflowContext,
        state: WorkflowStateDefinition,
        selection_id: str,
    ):
        validator = runtime_registry.get_validator(state.validator)
        if not validator:
            return None
        return await validator(context, state, selection_id)

    def _build_menu_ui(
        self,
        state: WorkflowStateDefinition,
        options: MenuResolverResult,
        definition: WorkflowDefinition,
    ) -> WorkflowUIModel:
        title = state.ui.get("title") if state.ui else None
        description = state.ui.get("description") if state.ui else None
        return WorkflowUIModel(
            type="menu",
            state=state.name,
            title=title or options.title or definition.title,
            description=description or options.description,
            items=options.items,
            pagination=options.pagination,
        )

    def _build_cached_ui(
        self, state: WorkflowStateDefinition, cache: Dict[str, Any]
    ) -> WorkflowUIModel:
        items = [WorkflowMenuItem(**item) for item in cache.get("items", [])]
        pagination = cache.get("pagination") or {}
        return WorkflowUIModel(
            type="menu",
            state=state.name,
            title=state.ui.get("title"),
            description=state.ui.get("description"),
            items=items,
            pagination=WorkflowPagination(
                page=int(pagination.get("page") or 1),
                page_size=int(pagination.get("page_size") or 5),
                has_more=bool(pagination.get("has_more")),
            ),
        )

    def _serialize_menu_cache(
        self, state: WorkflowStateDefinition, options: MenuResolverResult, search_term: str | None
    ) -> Dict[str, Any]:
        pagination = options.pagination or WorkflowPagination(page=1, page_size=5, has_more=False)
        start_index = (pagination.page - 1) * pagination.page_size + 1
        return {
            "state": state.name,
            "items": [item.model_dump() for item in options.items],
            "pagination": pagination.model_dump(),
            "start_index": start_index,
            "search_term": search_term,
        }

    def _match_selection(self, user_input: str, items: list[dict[str, Any]], start_index: int) -> Optional[dict[str, Any]]:
        normalized = user_input.strip().lower()
        if not normalized:
            return None
        if normalized.isdigit():
            idx = int(normalized) - start_index
            if 0 <= idx < len(items):
                return items[idx]
        for item in items:
            label = (item.get("label") or "").strip()
            metadata = item.get("metadata") or {}
            if normalized == (item.get("id") or "").lower():
                return item
            if normalized == label.lower():
                return item
            candidate_names = []
            extracted = self._extract_name_from_label(label)
            if extracted:
                candidate_names.append(extracted)
            meta_name = metadata.get("name")
            if isinstance(meta_name, str):
                candidate_names.append(meta_name)
            for name in candidate_names:
                if normalized == name.strip().lower():
                    return item
        partial_matches: list[dict[str, Any]] = []
        for item in items:
            label_lower = (item.get("label") or "").strip().lower()
            metadata = item.get("metadata") or {}
            if normalized in label_lower:
                partial_matches.append(item)
                continue
            meta_name = metadata.get("name")
            if isinstance(meta_name, str) and normalized in meta_name.strip().lower():
                partial_matches.append(item)
                continue
            extracted = self._extract_name_from_label(item.get("label") or "")
            if extracted and normalized in extracted.lower():
                partial_matches.append(item)
        if len(partial_matches) == 1:
            return partial_matches[0]
        return None

    def _menu_prompt(self, state: WorkflowStateDefinition, options: MenuResolverResult, search_term: str | None) -> str:
        title = state.ui.get("title") if state.ui else ""
        prompt = title or f"Select an option for {state.capture_key or state.name}"
        lines = [prompt]
        if search_term:
            lines.append(f"Showing results matching '{search_term}'.")
        pagination = options.pagination or WorkflowPagination(page=1, page_size=5, has_more=False)
        start_index = (pagination.page - 1) * pagination.page_size + 1
        for offset, item in enumerate(options.items):
            number = start_index + offset
            lines.append(f"{number}. {item.label}")
        guidance = "Reply with the number or id of your choice."
        guidance += " Type a name or keyword to search."
        if pagination.has_more:
            guidance += " Type 'more' to load additional options."
        if state.optional:
            guidance += " Type 'skip' to keep the current default."
        lines.append(guidance)
        return "\n".join(lines)

    def _is_count_request(self, user_input: str) -> bool:
        normalized = user_input.strip().lower()
        return any(keyword in normalized for keyword in self.count_keywords)

    def _extract_search_term(self, user_input: str) -> str | None:
        cleaned = user_input.strip()
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered in self.more_commands or lowered in self.list_commands:
            return None
        if lowered in self.skip_commands:
            return None
        if self._is_count_request(lowered):
            return None
        
        # Exclude common universal commands and greetings from search
        global_commands = {"cancel", "stop", "reset", "exit", "hello", "hi", "help"}
        if lowered in global_commands:
            return None

        if cleaned.isdigit():
            return None
        return cleaned

    @staticmethod
    def _extract_name_from_label(label: str) -> str | None:
        if not label:
            return None
        match = re.search(r"\(([^)]+)\)\s*$", label)
        if match:
            return match.group(1).strip()
        return None

    async def _count_message(self, state: WorkflowStateDefinition, context: WorkflowContext) -> str:
        resolver = runtime_registry.get_count_resolver(state.count_resolver)
        if not resolver:
            return "I couldn't determine the total from here. Please continue by picking from the menu."
        try:
            count = await resolver(context, state)
        except Exception:  # noqa: BLE001
            logger.exception("Count resolver failed for state %s", state.name)
            return "I couldn't determine the total from here. Please continue by picking from the menu."
        if count is None:
            return "I couldn't determine the total from here. Please continue by picking from the menu."
        return f"There are {count} options available right now. Continue by picking the number or id."


class InputStep(BaseStep):
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        capture_key = state.capture_key or state.config.get("capture")
        if not capture_key:
            logger.error("Input state %s missing capture key.", state.name)
            return StepResult(advance=True)
        user_input = (context.user_input or "").strip()
        if state.optional and user_input.lower() == "skip":
            return StepResult(advance=True, message=f"Skipped {capture_key}.")
        if not user_input:
            return StepResult(
                ui=WorkflowUIModel(
                    type="input",
                    state=state.name,
                    title=state.ui.get("title") if state.ui else definition.title,
                    description=state.ui.get("description"),
                ),
                await_input=True,
                message=state.ui.get("description"),
            )
        validator = runtime_registry.get_validator(state.validator)
        if validator:
            validation = await validator(context, state, user_input)
            if not validation.valid:
                return StepResult(
                    ui=WorkflowUIModel(
                        type="input",
                        state=state.name,
                        title=state.ui.get("title") if state.ui else definition.title,
                        description=validation.message or state.ui.get("description"),
                    ),
                    await_input=True,
                    message=validation.message,
                )
        return StepResult(
            updates={capture_key: user_input},
            advance=True,
            message=f"{capture_key} captured.",
        )


class ConfirmationStep(BaseStep):
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        summary = {}
        for key in context.collected_data:
            summary[key] = context.collected_data[key]
        ui = WorkflowUIModel(
            type="confirmation",
            state=state.name,
            title=state.ui.get("title") if state.ui else "Confirm details",
            description=state.ui.get("description"),
            summary=summary,
            options=state.options or ["confirm", "cancel"],
        )
        user_input = (context.user_input or "").strip().lower()
        if not user_input:
            return StepResult(
                ui=ui, await_input=True, message="Review and reply with confirm/cancel."
            )
        if user_input in {"confirm", "yes", "submit"}:
            return StepResult(
                advance=True, message="Confirmed.", next_state=state.next_state
            )
        if user_input in {"cancel", "stop"}:
            return StepResult(
                message="Workflow canceled.",
                completed=True,
            )
        return StepResult(
            ui=ui, await_input=True, message="Please type confirm or cancel."
        )


class DbWriteStep(BaseStep):
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        action = runtime_registry.get_action(state.action)
        if not action:
            logger.error("DB write step %s missing action.", state.name)
            return StepResult(
                message="Workflow is not configured for persistence.", completed=True
            )
        message = await action(context, state)
        return StepResult(message=message, advance=True)


class EndStep(BaseStep):
    async def run(
        self,
        definition: WorkflowDefinition,
        state: WorkflowStateDefinition,
        context: WorkflowContext,
    ) -> StepResult:
        return StepResult(
            ui=WorkflowUIModel(
                type="end",
                state=state.name,
                title=definition.title,
                description="Workflow complete.",
            ),
            completed=True,
            message="Workflow finished.",
        )


class WorkflowExecutor:
    def __init__(self, definitions_dir: str) -> None:
        definitions_path = Path(definitions_dir)
        self.registry = WorkflowRegistry(definitions_path)
        self.handlers: Dict[str, BaseStep] = {
            "system": SystemStep(),
            "menu": MenuStep(),
            "input": InputStep(),
            "confirmation": ConfirmationStep(),
            "db_write": DbWriteStep(),
            "end": EndStep(),
        }

    def has_workflow(self, workflow_id: str) -> bool:
        return self.registry.has_workflow(workflow_id)

    async def is_active(self, workflow_id: str, session_id: str) -> bool:
        try:
            definition = self.registry.get(workflow_id)
        except KeyError:
            return False
        store = WorkflowSessionStore()
        snapshot = await store.load(session_id, definition.workflow_id)
        return bool(snapshot.current_state)

    async def get_active_workflow(self, session_id: str) -> Optional[str]:
        store = WorkflowSessionStore()
        for workflow_id, definition in self.registry.registry.items():
            snapshot = await store.load(session_id, definition.workflow_id)
            if snapshot.current_state:
                return workflow_id
        return None

    async def cancel(self, workflow_id: str, session_id: str) -> None:
        store = WorkflowSessionStore()
        await store.clear(session_id, workflow_id)

    async def execute(
        self,
        *,
        workflow_id: str,
        session_id: str,
        user_id: str,
        user_role: Optional[str],
        user_input: Optional[str],
        services: Optional[Dict[str, Any]] = None,
        prefill: Optional[Dict[str, Any]] = None,
    ) -> WorkflowTurn:
        definition = self.registry.get(workflow_id)
        store = WorkflowSessionStore()
        snapshot = await store.load(session_id, definition.workflow_id)
        
        if prefill:
            for key, value in prefill.items():
                if value in (None, "", []):
                    continue
                if not self._is_valid_prefill(key, value):
                    continue
                snapshot.collected_data[key] = value

        context = WorkflowContext(
            session_id=session_id,
            workflow_id=definition.workflow_id,
            user_id=user_id,
            user_role=user_role,
            user_input=user_input,
            services=services or {},
            collected_data=snapshot.collected_data,
            menu_cache=snapshot.menu_cache,
        )
        current_state = snapshot.current_state or definition.start_state
        
        # If workflow is just starting (no saved state), clear user_input
        # to prevent the trigger phrase from being treated as search input
        is_new_workflow = snapshot.current_state is None
        logger.info(f"Workflow starting: is_new={is_new_workflow}, current_state={snapshot.current_state}, user_input='{user_input}'")
        if is_new_workflow:
            logger.info("Clearing user_input for new workflow")
            context.user_input = None
        
        iterations = 0
        while iterations < 20:
            iterations += 1
            state = definition.get_state(current_state)
            handler = self.handlers.get(state.type)
            if not handler:
                logger.error("No handler for workflow state type %s.", state.type)
                break

            logger.info(f"Running state '{current_state}' (type={state.type}) with user_input='{context.user_input}'")
            result = await handler.run(definition, state, context)
            snapshot.collected_data.update(result.updates)
            if result.menu_cache is not None:
                snapshot.menu_cache = result.menu_cache
                context.menu_cache = snapshot.menu_cache
            reply_text = result.message or ""

            if result.completed:
                await store.clear(session_id, definition.workflow_id)
                payload = WorkflowPayload(
                    workflow_id=definition.workflow_id,
                    state=current_state,
                    ui=result.ui,
                    collected_data=snapshot.collected_data,
                    completed=True,
                )
                return WorkflowTurn(
                    reply=reply_text or "Workflow completed.",
                    payload=payload,
                    completed=True,
                )

            if result.await_input:
                snapshot.current_state = current_state
                await store.save(snapshot)
                payload = WorkflowPayload(
                    workflow_id=definition.workflow_id,
                    state=current_state,
                    ui=result.ui,
                    collected_data=snapshot.collected_data,
                    completed=False,
                )
                return WorkflowTurn(
                    reply=reply_text or self._format_reply(result.ui),
                    payload=payload,
                    completed=False,
                )

            next_state = result.next_state or state.next_state
            if not next_state:
                await store.clear(session_id, definition.workflow_id)
                payload = WorkflowPayload(
                    workflow_id=definition.workflow_id,
                    state=current_state,
                    ui=result.ui,
                    collected_data=snapshot.collected_data,
                    completed=True,
                )
                return WorkflowTurn(
                    reply=reply_text or "Workflow completed.",
                    payload=payload,
                    completed=True,
                )

            current_state = next_state
            snapshot.current_state = current_state
            # Clear user input when advancing to prevent it from polluting the next state
            # This is especially important after system steps that auto-advance
            context.user_input = None

        logger.warning("Workflow %s exceeded iteration limit.", definition.workflow_id)
        await store.save(snapshot)
        payload = WorkflowPayload(
            workflow_id=definition.workflow_id,
            state=current_state,
            ui=None,
            collected_data=snapshot.collected_data,
            completed=False,
        )
        return WorkflowTurn(
            reply="Workflow paused due to internal error.",
            payload=payload,
            completed=False,
        )

    def _format_reply(self, ui: Optional[WorkflowUIModel]) -> str:
        if not ui:
            return "Provide more details."
        if ui.type == "menu":
            return ui.title or "Choose an option."
        if ui.type == "input":
            return ui.description or "Provide input."
        if ui.type == "confirmation":
            return "Review details and confirm."
        return "Provide more details."

    def _is_valid_prefill(self, key: str, value: Any) -> bool:
        if key == "scheduled_ref_no":
            text = str(value).strip().lower()
            return bool(re.fullmatch(r"[a-z0-9-]{16,}", text))
        if key.endswith("_id") or key in {"company_id", "task_est_time"}:
            return str(value).strip().isdigit()
        return True
