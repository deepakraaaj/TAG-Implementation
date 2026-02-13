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
    intent_analysis: Dict[str, Any]
    toon_data: Dict[str, Any] 
    from_cache: bool 
    query_understanding: Dict[str, Any]
    workflow_payload: Dict[str, Any] # To store UI payload from workflow
