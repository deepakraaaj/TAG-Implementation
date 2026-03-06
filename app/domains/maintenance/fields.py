"""Field metadata for the maintenance domain."""

# Human-readable field labels
FIELD_LABELS = {
    "sche_details_id": "Scheduler",
    "task_for": "Task For",
    "facility_id_or_name": "Facility",
    "asset_id_or_name": "Asset",
    "assigned_user": "User",
    "task_description_id": "Task",
    "priority": "Priority",
    "task_est_time": "Task EST (Mins)",
    "scheduled_ref_no": "Schedule Ref",
}

# Dropdown options for specific fields
FIELD_OPTIONS = {
    "task_for": [
        {"label": "Facility", "value": "facility"},
        {"label": "Asset", "value": "asset"},
    ],
    "status": [
        {"label": "Pending", "value": "Pending"},
        {"label": "In Progress", "value": "In Progress"},
        {"label": "Completed", "value": "Completed"},
        {"label": "Overdue", "value": "Overdue"},
    ],
    "facility_status": [
        {"label": "Assigned", "value": "Assigned"},
        {"label": "In Progress", "value": "In Progress"},
        {"label": "Overdue", "value": "Overdue"},
        {"label": "Delay In Progress", "value": "Delay In Progress"},
        {"label": "Completed", "value": "Completed"},
    ],
    "priority": [
        {"label": "High", "value": "High"},
        {"label": "Medium", "value": "Medium"},
        {"label": "Low", "value": "Low"},
    ],
    "occurrence": [
        {"label": "Daily", "value": "1"},
        {"label": "Weekly", "value": "2"},
        {"label": "Monthly", "value": "3"},
        {"label": "Quarterly", "value": "4"},
    ],
    "is_active": [
        {"label": "Yes", "value": "1"},
        {"label": "No", "value": "0"},
    ],
}

# Lookup table configurations
LOOKUP_CONFIGS = {
    "sche_details_id": {
        "table": "scheduler_details",
        "value_column": "id",
        "display_columns": ["name", "time", "schedule_time", "start_time", "date", "occurrence"],
        "search_columns": ["id", "name", "time", "schedule_time", "start_time"],
        "order_by": "id DESC",
        "title": "Choose a scheduler",
    },
    "facility_id_or_name": {
        "table": "facility",
        "value_column": "id",
        "display_columns": ["id", "name", "code", "is_active"],
        "search_columns": ["id", "name", "code"],
        "order_by": "name ASC",
        "title": "Choose a facility",
    },
    "asset_id_or_name": {
        "table": "asset",
        "value_column": "id",
        "display_columns": ["id", "name", "code", "is_active"],
        "search_columns": ["id", "name", "code"],
        "order_by": "name ASC",
        "title": "Choose an asset",
    },
    "assigned_user": {
        "table": "user",
        "value_column": "id",
        "display_columns": ["id", "first_name", "last_name", "is_active"],
        "search_columns": ["id", "first_name", "last_name"],
        "order_by": "first_name ASC, last_name ASC",
        "title": "Choose a user",
    },
    "task_description_id": {
        "table": "task_description",
        "value_column": "id",
        "display_columns": ["id", "name", "is_active"],
        "search_columns": ["id", "name"],
        "order_by": "name ASC",
        "title": "Choose a task",
    },
}
