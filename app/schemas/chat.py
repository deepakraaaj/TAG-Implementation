from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Literal

class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: Optional[str] = None
    user_role: Optional[str] = "user"
    metadata: Optional[Dict[str, Any]] = {}

class SQLResponse(BaseModel):
    ran: bool = False
    cached: bool = False
    query: Optional[str] = None
    row_count: Optional[int] = None
    rows_preview: Optional[List[Dict[str, Any]]] = None

class ChatResponse(BaseModel):
    session_id: str
    message: str
    status: Literal["ok", "needs_filters", "workflow_active", "cancelled", "error"]
    labels: List[str] = []
    # workflow: Optional[WorkflowResponse] = None # Simpler for now
    sql: Optional[SQLResponse] = None
    toon: Optional[Dict[str, Any]] = None
    provider_used: str = "tag_backend"
    trace_id: str = ""
