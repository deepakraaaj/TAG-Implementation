from typing import Any, Dict, List, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    messages: List[BaseMessage]
    metadata: Dict[str, Any]
    route: str
    intermediate_frame: Dict[str, Any]
    intent: Dict[str, Any]
    sql_query: str
    sql_result: str
    row_count: int
    rows_preview: List[Dict[str, Any]]
    total_records: int
    error: str
    workflow_payload: Dict[str, Any]
    report_result: Dict[str, Any]
    pending_select: Dict[str, Any]
    token_usage: Dict[str, Any]
    evidence_bundle: Dict[str, Any]
    verification_report: Dict[str, Any]
    validation_report: Dict[str, Any]
