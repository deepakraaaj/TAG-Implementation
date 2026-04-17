"""Field metadata for the standard reference domain."""

FIELD_LABELS = {
    "title": "Work Order Title",
    "status": "Status",
    "priority": "Priority",
    "assignee_id": "Assignee",
    "location_id": "Site",
    "asset_id": "Asset",
    "scheduled_date": "Scheduled Date",
    "date_created": "Created At",
    "date_updated": "Updated At",
    "is_active": "Active",
}

FIELD_OPTIONS = {
    "status": [
        {"label": "Open", "value": "Open"},
        {"label": "In Progress", "value": "In Progress"},
        {"label": "Done", "value": "Done"},
        {"label": "Cancelled", "value": "Cancelled"},
    ],
    "priority": [
        {"label": "Critical", "value": "Critical"},
        {"label": "High", "value": "High"},
        {"label": "Medium", "value": "Medium"},
        {"label": "Low", "value": "Low"},
    ],
    "is_active": [
        {"label": "Yes", "value": "1"},
        {"label": "No", "value": "0"},
    ],
}

LOOKUP_CONFIGS = {
    "assignee_id": {
        "table": "person",
        "value_column": "id",
        "display_columns": ["id", "first_name", "last_name", "email", "is_active"],
        "search_columns": ["id", "first_name", "last_name", "email"],
        "order_by": "id DESC",
        "title": "Choose assignee",
    },
    "location_id": {
        "table": "location",
        "value_column": "id",
        "display_columns": ["id", "name", "code", "is_active"],
        "search_columns": ["id", "name", "code"],
        "order_by": "id DESC",
        "title": "Choose site",
    },
    "asset_id": {
        "table": "asset",
        "value_column": "id",
        "display_columns": ["id", "asset_code", "name", "asset_type", "is_active"],
        "search_columns": ["id", "asset_code", "name"],
        "order_by": "id DESC",
        "title": "Choose asset",
    },
}
