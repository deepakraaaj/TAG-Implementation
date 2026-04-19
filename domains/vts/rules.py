"""Manual domain hooks for generated domain packages."""

import re
from typing import Any, Dict, Optional


def is_flow_candidate(query: str, domain: Any) -> bool:
    """Detect if query should trigger a write flow (create/update)."""
    if not query:
        return False

    create_patterns = [
        r'\b(create|add|new|make)\s+(trip|voyage)\b',
        r'\b(start|begin|schedule)\s+(a\s+)?(trip|voyage)\b',
    ]

    update_patterns = [
        r'\b(update|change|set|modify)\s+(trip|voyage)?\s*(status|state)\b',
        r'\b(mark|mark as)\s+(completed|done|reached|cancelled|canceled)\b',
    ]

    query_lower = query.lower()

    for pattern in create_patterns:
        if re.search(pattern, query_lower):
            return True

    for pattern in update_patterns:
        if re.search(pattern, query_lower):
            return True

    return False


def format_no_records_message(
    entity: str, filters: Optional[Dict[str, Any]] = None, domain: Optional[Any] = None
) -> str:
    """Format a friendly message when no records are found."""
    if not entity:
        return "No records found."

    entity_label = entity.lower().rstrip('s')

    if filters and 'date' in str(filters).lower():
        return f"No {entity} found for the specified date range."

    if filters and 'status' in str(filters).lower():
        return f"No {entity} found with the specified status."

    return f"No {entity} found."


def resolve_flow_slot_prefill(
    query: str, flow_id: str, domain: Optional[Any] = None
) -> Dict[str, Any]:
    """Extract NL hints to prefill flow form fields."""
    prefill = {}

    if not query:
        return prefill

    query_lower = query.lower()

    vehicle_match = re.search(r'(?:vehicle|truck|bus|car)\s+(?:number\s+)?([A-Za-z0-9\-]+)', query_lower)
    if vehicle_match:
        prefill['vehicle_number_hint'] = vehicle_match.group(1).upper()

    trip_name_match = re.search(r'(?:trip|voyage|run)\s+(?:named?|called)\s+["\']?([^"\']+)["\']?', query)
    if trip_name_match:
        prefill['trip_name_hint'] = trip_name_match.group(1).strip()

    return prefill
