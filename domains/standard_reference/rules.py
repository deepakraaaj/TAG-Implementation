"""Business rules for the standard reference domain."""

from typing import Any, Dict, List


def apply_conditional_fields(
    table: str,
    required_fields: List[str],
    collected_fields: Dict[str, Any],
    config: Dict[str, Any] | None = None,
) -> List[str]:
    """Apply minimal example field visibility rules for write flows."""
    _ = config
    if str(table or "").strip() != "work_item":
        return required_fields

    priority = str((collected_fields or {}).get("priority") or "").strip().lower()
    if priority == "critical" and "scheduled_date" not in required_fields:
        return list(required_fields) + ["scheduled_date"]
    return required_fields


def is_flow_candidate(message: str, table: str, config: Dict[str, Any] | None = None) -> bool:
    """Enable the example write flow only for clear create-style requests."""
    _ = config
    normalized_message = str(message or "").strip().lower()
    normalized_table = str(table or "").strip().lower()
    if normalized_table != "work_item":
        return False
    return any(token in normalized_message for token in ("create", "add", "new"))


def format_no_records_message(context: Dict[str, Any]) -> str:
    """Return a friendlier empty-state message for the main entity."""
    entity_label = str((context or {}).get("entity_label") or "").strip().lower()
    if entity_label in {"work order", "work orders", "work item", "work items"}:
        return "No work orders matched the selected filters."
    return ""
