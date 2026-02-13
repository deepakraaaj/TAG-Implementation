from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.workflow.engine.ui import WorkflowMenuItem, WorkflowPagination


@dataclass
class WorkflowContext:
    session_id: str
    workflow_id: str
    user_id: str
    user_role: Optional[str]
    user_input: Optional[str]
    # Removed db_session in favor of SchemaService or explicit services
    # But for compatibility with steps, we might need a way to pass services
    services: Dict[str, Any] = field(default_factory=dict) 
    collected_data: Dict[str, Any] = field(default_factory=dict)
    menu_cache: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MenuResolverResult:
    items: List[WorkflowMenuItem]
    pagination: WorkflowPagination
    title: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ValidationResult:
    valid: bool
    message: Optional[str] = None
