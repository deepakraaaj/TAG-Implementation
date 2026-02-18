"""Enum mappings for the maintenance domain."""

# Value to integer mappings (for INSERT/UPDATE)
ENUM_MAPPINGS = {
    "status": {
        "pending": 0,
        "inprogress": 1,
        "completed": 2,
        "overdue": 3,
    },
    "facility_status": {
        "assigned": 0,
        "inprogress": 1,
        "overdue": 2,
        "delayinprogress": 3,
        "completed": 4,
    },
}

# Integer to label mappings (for SELECT display)
ENUM_LABELS = {
    "status": {
        0: "Pending",
        1: "In Progress",
        2: "Completed",
        3: "Overdue",
    },
    "facility_status": {
        0: "Assigned",
        1: "In Progress",
        2: "Overdue",
        3: "Delay In Progress",
        4: "Completed",
    },
}
