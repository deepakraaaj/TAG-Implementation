from typing import Any, Dict, List, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    messages: List[BaseMessage]
    metadata: Dict[str, Any]
    route: str
    intent: Dict[str, Any]
    sql_query: str
    sql_result: str
    row_count: int
    rows_preview: List[Dict[str, Any]]
    error: str
    workflow_payload: Dict[str, Any]
    token_usage: Dict[str, Any]
