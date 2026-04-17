"""Business rules for the starter domain."""
from typing import Any, Dict, List


def apply_conditional_fields(
    table: str,
    required_fields: List[str],
    collected_fields: Dict[str, Any],
    config: Dict[str, Any] | None = None,
) -> List[str]:
    """
    Apply domain-specific field visibility rules.
    Starter keeps fields as-is.
    """
    _ = table
    _ = collected_fields
    _ = config
    return required_fields


def is_flow_candidate(message: str, table: str, config: Dict[str, Any] | None = None) -> bool:
    """
    Determine if a message should trigger a declarative flow.
    Starter domain disables flow auto-trigger by default.
    """
    _ = message
    _ = table
    _ = config
    return False


def format_no_records_message(context: Dict[str, Any]) -> str:
    """
    Optional no-record formatter.
    Return empty string to let shared fallback logic build filter-aware messages.
    """
    _ = context
    return ""
