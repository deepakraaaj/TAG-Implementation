"""Business rules for the maintenance domain."""
import re
from typing import Any, Dict, List


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
        # Trigger flow for schedule-related queries
        return bool(re.search(r"\b(schedule|scheduler|scheduled)\b", message.lower()))
    
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
                assignee_name = str(metadata.get("user_name") or "").strip()
                if assignee_name:
                    first = assignee_name.split()[0].strip() or assignee_name
                    template = str(response_messages.get("self_no_records_today", "")).strip()
                    if template:
                        return template.replace("{name}", first)
                    return f"{first}, you don't have tasks today."

    # Return empty string so the shared ResponseNode fallback can still
    # include parsed filter details for non-specialized cases.
    return ""
