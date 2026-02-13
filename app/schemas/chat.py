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
    status: Literal["ok", "error"]
    labels: List[str] = []
    sql: Optional[SQLResponse] = None
    token_usage: Optional[Dict[str, int]] = None
    provider_used: str = "tag_backend"
    trace_id: str = ""
