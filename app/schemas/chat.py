from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal

class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: Optional[str] = None
    user_role: Optional[str] = "user"
    idempotency_key: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class SQLResponse(BaseModel):
    ran: bool = False
    cached: bool = False
    query: Optional[str] = None
    row_count: Optional[int] = None
    rows_preview: Optional[List[Dict[str, Any]]] = None
    rows_preview_toon: Optional[str] = None
    rows_preview_encoding: Optional[str] = None

class ChatResponse(BaseModel):
    session_id: str
    message: str
    status: Literal["ok", "error"]
    labels: List[str] = Field(default_factory=list)
    sql: Optional[SQLResponse] = None
    token_usage: Optional[Dict[str, int]] = None
    token_details: Optional[Dict[str, Any]] = None
    provider_used: str = "tag_backend"
    trace_id: str = ""
    stage_timings_ms: Optional[Dict[str, float]] = None
