"""Business rules for the maintenance domain."""
import re
from typing import Any, Dict, List


def _company_name(metadata: Dict[str, Any]) -> str:
    company_obj = metadata.get("company")
    company_obj_name = ""
    if isinstance(company_obj, dict):
        company_obj_name = str(company_obj.get("name") or "").strip()
    for candidate in (
        metadata.get("company_name"),
        metadata.get("companyName"),
        company_obj_name,
    ):
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned
    return ""


def _self_display_name(metadata: Dict[str, Any]) -> str:
    assignee_name = str(metadata.get("user_name") or "").strip()
    if not assignee_name:
        return ""
    lowered = assignee_name.casefold()
    if lowered in {"user", "unknown", "na", "n/a", "null", "none"}:
        return ""
    company_name = _company_name(metadata)
    if company_name and lowered == company_name.casefold():
        return ""
    first = assignee_name.split()[0].strip()
    return first if first else assignee_name


def apply_conditional_fields(table: str, required_fields: List[str], collected_fields: Dict[str, Any]) -> List[str]:
    """
    Apply domain-specific field visibility rules.
    
    Args:
        table: Target table name
        required_fields: List of required field names
        collected_fields: Currently collected field values
        
    Returns:
        Filtered list of required fields based on business logic
    """
    if table == "scheduler_task_details":
        task_for = str(collected_fields.get("task_for", "")).strip().lower()
        if task_for == "facility":
            # Remove asset field if task is for facility
            return [f for f in required_fields if f != "asset_id_or_name"]
    
    return required_fields


def is_flow_candidate(message: str, table: str) -> bool:
    """
    Determine if a message should trigger a declarative flow.
    
    Args:
        message: User's message
        table: Resolved table name
        
    Returns:
        True if flow should be triggered
    """
    if table == "scheduler_task_details":
        msg = str(message or "").lower()
        # Trigger flow for schedule-related queries.
        if re.search(r"\b(schedule|scheduler|scheduled)\b", msg):
            return True

        # Also trigger schedule/create flow for explicit task-creation phrasing.
        # Examples: "create a task for nirmala", "assign a maintenance task".
        create_intent = bool(re.search(r"\b(create|add|assign|new)\b", msg))
        task_intent = bool(re.search(r"\b(task|tasks|work\s*order|workorder|maintenance)\b", msg))
        read_intent = bool(re.search(r"\b(show|list|get|find|view|count|summary|summarize|how many|what|which)\b", msg))
        if create_intent and task_intent and not read_intent:
            return True
    
    return False


def format_no_records_message(context: Dict[str, Any]) -> str:
    """
    Domain-specific no-record wording hook.

    Args:
        context: {
            "sql": str,
            "metadata": dict,
            "response_messages": dict
        }
    """
    sql = str((context or {}).get("sql", "") or "")
    metadata = dict((context or {}).get("metadata") or {})
    response_messages = dict((context or {}).get("response_messages") or {})
    lowered = sql.lower()
    if "date(scheduled_date) = curdate()" in lowered:
        id_match = re.search(r"\bassigned_user_id\s*=\s*(\d+)", sql, flags=re.IGNORECASE)
        if id_match:
            sql_uid = str(id_match.group(1) or "").strip()
            meta_uid = str(metadata.get("user_id") or metadata.get("userId") or "").strip()
            if meta_uid and sql_uid == meta_uid:
                display_name = _self_display_name(metadata)
                template = str(response_messages.get("self_no_records_today", "")).strip()
                if display_name:
                    if template:
                        return template.replace("{name}", display_name)
                    return f"{display_name}, you don't have tasks today."
                if template and "{name}" not in template:
                    return template
                return "You don't have tasks today."

    # Return empty string so the shared ResponseNode fallback can still
    # include parsed filter details for non-specialized cases.
    return ""
