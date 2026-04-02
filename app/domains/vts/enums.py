ENUM_MAPPINGS = {
    "status": {
        "open": 0,
        "inprogress": 1,
        "done": 2,
        "completed": 2
    },
    "exception_type": {
        "overspeed": 1,
        "routedeviation": 2,
        "halt": 3
    },
    "is_active": {
        "yes": 1,
        "active": 1,
        "no": 0,
        "inactive": 0
    },
    "is_over_speed": {
        "yes": 1,
        "no": 0
    }
}

ENUM_LABELS = {
    "status": {
        0: "Open",
        1: "In Progress",
        2: "Done"
    },
    "exception_type": {
        1: "Overspeed",
        2: "Route Deviation",
        3: "Halt"
    }
}
