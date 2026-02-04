from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    messages: List[BaseMessage]
    sql_query: str
    sql_result: str
    row_count: int
    rows_preview: List[Dict]
    error: str
    retry_count: int
    metadata: Dict[str, Any]
    route: str
    rewritten_query: str
    toon_data: Dict[str, Any] # New field for contextualized query
    from_cache: bool # Track if result came from cache
