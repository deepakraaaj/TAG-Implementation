"""Enum mappings for the standard reference domain."""

ENUM_MAPPINGS = {
    "status": {
        "open": 0,
        "inprogress": 1,
        "done": 2,
        "cancelled": 3,
    },
    "priority": {
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 4,
    },
}

ENUM_LABELS = {
    "status": {
        0: "Open",
        1: "In Progress",
        2: "Done",
        3: "Cancelled",
    },
    "priority": {
        1: "Low",
        2: "Medium",
        3: "High",
        4: "Critical",
    },
}
