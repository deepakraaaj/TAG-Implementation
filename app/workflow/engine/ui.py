from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class WorkflowPagination(BaseModel):
    page: int = 1
    page_size: int = 5
    has_more: bool = False


class WorkflowMenuItem(BaseModel):
    id: str
    label: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkflowFormField(BaseModel):
    id: str
    label: str
    type: Literal["text", "number", "date", "select", "textarea"]
    options: Optional[List[str]] = None


class WorkflowUIModel(BaseModel):
    type: Literal["menu", "form", "confirmation", "message", "input", "end"]
    state: str
    title: Optional[str] = None
    description: Optional[str] = None
    items: List[WorkflowMenuItem] = []
    pagination: Optional[WorkflowPagination] = None
    fields: List[WorkflowFormField] = []
    summary: Optional[Dict[str, Any]] = None
    options: Optional[List[str]] = None


class WorkflowPayload(BaseModel):
    workflow_id: str
    state: str
    ui: Optional[WorkflowUIModel] = None
    collected_data: Dict[str, Any]
    completed: bool = False
