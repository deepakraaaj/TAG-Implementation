"""Enum mappings for the starter domain."""

# Value to integer mappings (for INSERT/UPDATE)
ENUM_MAPPINGS = {
    "status": {
        "open": 0,
        "inprogress": 1,
        "done": 2,
    }
}

# Integer to label mappings (for SELECT display)
ENUM_LABELS = {
    "status": {
        0: "Open",
        1: "In Progress",
        2: "Done",
    }
}

