"""Field labels, options, and lookup configurations for domain forms."""

FIELD_LABELS = {
    "name": "Trip Name",
    "vehicle_id": "Vehicle",
    "location_id": "Location / Terminal",
    "route_id": "Route",
    "scheduled_date": "Scheduled Date",
    "notify_mobile_number": "Notify Mobile Number",
    "type": "Trip Type",
    "recent_state_id": "Trip Status",
    "is_active": "Active",
}

FIELD_OPTIONS = {
    "type": [
        {"label": "Dynamic", "value": 2},
        {"label": "Terminal", "value": 1},
    ],
    "recent_state_id": [
        {"label": "Created", "value": 10},
        {"label": "Vehicle Entered", "value": 20},
        {"label": "En route", "value": 30},
        {"label": "Reached", "value": 40},
        {"label": "Destination Exit", "value": 50},
        {"label": "Invoice Received", "value": 60},
        {"label": "Cancel", "value": 70},
    ],
    "is_active": [
        {"label": "Active", "value": 1},
        {"label": "Inactive", "value": 0},
    ],
}

LOOKUP_CONFIGS = {
    "vehicle_id": {
        "table": "vehicle",
        "display_field": "vehicle_number",
        "search_column": "vehicle_number",
        "scope_column": "company_id",
    },
    "location_id": {
        "table": "location",
        "display_field": "name",
        "search_column": "name",
        "scope_column": None,
    },
    "route_id": {
        "table": "route",
        "display_field": "route_code",
        "search_column": "route_code",
        "scope_column": "company_id",
    },
}
