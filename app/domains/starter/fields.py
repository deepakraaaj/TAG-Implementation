"""Field metadata for the starter domain."""

# Human-readable field labels
FIELD_LABELS = {
    "title": "Title",
    "status": "Status",
    "priority": "Priority",
    "assignee_id": "Assignee",
    "location_id": "Location",
    "scheduled_date": "Scheduled Date",
}

# Dropdown options for specific fields
FIELD_OPTIONS = {
    "status": [
        {"label": "Open", "value": "Open"},
        {"label": "In Progress", "value": "In Progress"},
        {"label": "Done", "value": "Done"},
    ],
    "priority": [
        {"label": "High", "value": "High"},
        {"label": "Medium", "value": "Medium"},
        {"label": "Low", "value": "Low"},
    ],
    "is_active": [
        {"label": "Yes", "value": "1"},
        {"label": "No", "value": "0"},
    ],
}

# Lookup table configurations
LOOKUP_CONFIGS = {
    "assignee_id": {
        "table": "person",
        "value_column": "id",
        "display_columns": ["id", "first_name", "last_name", "is_active"],
        "search_columns": ["id", "first_name", "last_name"],
        "order_by": "id DESC",
        "title": "Choose assignee",
    },
    "location_id": {
        "table": "location",
        "value_column": "id",
        "display_columns": ["id", "name", "code", "is_active"],
        "search_columns": ["id", "name", "code"],
        "order_by": "id DESC",
        "title": "Choose location",
    },
}

