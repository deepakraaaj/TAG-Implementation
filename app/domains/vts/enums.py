"""Enum mappings for VTS domain columns."""

ENUM_MAPPINGS = {
    "recent_state_id": {
        "created": 10,
        "vehicle_entered": 20,
        "en_route": 30,
        "reached": 40,
        "destination_exit": 50,
        "invoice_received": 60,
        "cancel": 70
    }
}

ENUM_LABELS = {
    "recent_state_id": {
        10: "Created",
        20: "Vehicle Entered",
        30: "En route",
        40: "Reached",
        50: "Destination Exit",
        60: "Invoice Received",
        70: "Cancel"
    }
}
