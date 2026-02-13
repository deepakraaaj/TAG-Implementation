from __future__ import annotations

from typing import Awaitable, Callable, Dict, Optional

from app.workflow.engine.dsl import WorkflowStateDefinition
from app.workflow.engine.types import MenuResolverResult, ValidationResult, WorkflowContext

ResolverCallable = Callable[
    [WorkflowContext, WorkflowStateDefinition, int, int, Optional[str]], Awaitable[MenuResolverResult]
]
ValidatorCallable = Callable[[WorkflowContext, WorkflowStateDefinition, str], Awaitable[ValidationResult]]
ActionCallable = Callable[[WorkflowContext, WorkflowStateDefinition], Awaitable[str]]
CountResolverCallable = Callable[
    [WorkflowContext, WorkflowStateDefinition], Awaitable[Optional[int]]
]


class Registry:
    def __init__(self) -> None:
        self.resolvers: Dict[str, ResolverCallable] = {}
        self.validators: Dict[str, ValidatorCallable] = {}
        self.actions: Dict[str, ActionCallable] = {}
        self.count_resolvers: Dict[str, CountResolverCallable] = {}

    def register_resolver(self, name: str, func: ResolverCallable) -> None:
        self.resolvers[name] = func

    def register_validator(self, name: str, func: ValidatorCallable) -> None:
        self.validators[name] = func

    def register_action(self, name: str, func: ActionCallable) -> None:
        self.actions[name] = func

    def register_count_resolver(self, name: str, func: CountResolverCallable) -> None:
        self.count_resolvers[name] = func

    def get_resolver(self, name: Optional[str]) -> Optional[ResolverCallable]:
        if not name:
            return None
        return self.resolvers.get(name)

    def get_validator(self, name: Optional[str]) -> Optional[ValidatorCallable]:
        if not name:
            return None
        return self.validators.get(name)

    def get_action(self, name: Optional[str]) -> Optional[ActionCallable]:
        if not name:
            return None
        return self.actions.get(name)

    def get_count_resolver(
        self, name: Optional[str]
    ) -> Optional[CountResolverCallable]:
        if not name:
            return None
        return self.count_resolvers.get(name)


runtime_registry = Registry()
