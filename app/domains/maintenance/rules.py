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
