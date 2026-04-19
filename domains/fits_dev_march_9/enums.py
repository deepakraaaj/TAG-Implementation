ENUM_MAPPINGS = {
    # Task & Maintenance (5)
    "task_transaction_status": {
        "pending": 0,
        "in_progress": 1,
        "in progress": 1,
        "completed": 2,
        "done": 2,
        "overdue": 3
    },
    "priority": {
        "low": 0,
        "medium": 1,
        "high": 2,
        "critical": 3
    },
    "facility_status": {
        "assigned": 0,
        "in_progress": 1,
        "in progress": 1,
        "overdue": 2,
        "delay_in_progress": 3,
        "delay in progress": 3,
        "completed": 4,
        "done": 4
    },
    "checklist_status": {
        "pending": 0,
        "in_progress": 1,
        "in progress": 1,
        "completed": 2,
        "overdue": 3
    },
    "process_status": {
        "new": 0,
        "proceeded": 1
    },

    # Transaction & Recording (3)
    "transaction_type": {
        "none": 0,
        "record": 1,
        "mapping": 2,
        "audit": 3
    },
    "recording_mode": {
        "none": 0,
        "manual": 1,
        "automatic": 2,
        "auto": 2
    },
    "process_type": {
        "none": 0,
        "default": 1
    },

    # Scheduling (2)
    "day_of_week": {
        "monday": 1,
        "tuesday": 2,
        "wednesday": 3,
        "thursday": 4,
        "friday": 5,
        "saturday": 6,
        "sunday": 7,
        "weekday": "1-5",
        "weekend": "6-7"
    },
    "exam_session_type": {
        "morning": 0,
        "afternoon": 1,
        "evening": 2
    },

    # Asset & Entity (3)
    "asset_scan_frequency": {
        "never": 0,
        "daily": 1,
        "weekly": 7,
        "monthly": 30,
        "quarterly": 90,
        "yearly": 365
    },
    "entity_type": {
        "asset": 0,
        "facility": 1,
        "location": 2
    },
    "role_type": {
        "none": 0,
        "super_admin": 1,
        "admin": 2,
        "supervisor": 3,
        "observer": 4,
        "help_desk": 5,
        "iot_service": 6,
        "client_integration": 7,
        "finance": 8
    },

    # System & Config (5)
    "notification_type": {
        "none": 0,
        "sms": 1,
        "email": 2,
        "vaiot": 3
    },
    "email_source": {
        "none": 0,
        "internal": 1,
        "external": 2
    },
    "application_service_type": {
        "fits": 0,
        "vts": 1,
        "ims": 2
    },
    "company_type": {
        "internal": 0,
        "customer": 1,
        "partner": 2
    },
    "content_type": {
        "image": 0,
        "video": 1,
        "document": 2,
        "audio": 3
    },

    # Common Fields
    "is_active": {
        "yes": 1,
        "active": 1,
        "no": 0,
        "inactive": 0
    },
    "is_open": {
        "yes": 1,
        "open": 1,
        "no": 0,
        "closed": 0
    }
}

ENUM_LABELS = {
    # Task & Maintenance (5)
    "task_transaction_status": {
        0: "Pending",
        1: "In Progress",
        2: "Completed",
        3: "Overdue"
    },
    "priority": {
        0: "Low",
        1: "Medium",
        2: "High",
        3: "Critical"
    },
    "facility_status": {
        0: "Assigned",
        1: "In Progress",
        2: "Overdue",
        3: "Delay In Progress",
        4: "Completed"
    },
    "checklist_status": {
        0: "Pending",
        1: "In Progress",
        2: "Completed",
        3: "Overdue"
    },
    "process_status": {
        0: "New",
        1: "Proceeded"
    },

    # Transaction & Recording (3)
    "transaction_type": {
        0: "None",
        1: "Record",
        2: "Mapping",
        3: "Audit"
    },
    "recording_mode": {
        0: "None",
        1: "Manual",
        2: "Automatic"
    },
    "process_type": {
        0: "None",
        1: "Default"
    },

    # Scheduling (2)
    "day_of_week": {
        1: "Monday",
        2: "Tuesday",
        3: "Wednesday",
        4: "Thursday",
        5: "Friday",
        6: "Saturday",
        7: "Sunday"
    },
    "exam_session_type": {
        0: "Morning",
        1: "Afternoon",
        2: "Evening"
    },

    # Asset & Entity (3)
    "asset_scan_frequency": {
        0: "Never",
        1: "Daily",
        7: "Weekly",
        30: "Monthly",
        90: "Quarterly",
        365: "Yearly"
    },
    "entity_type": {
        0: "Asset",
        1: "Facility",
        2: "Location"
    },
    "role_type": {
        0: "None",
        1: "Super Admin",
        2: "Admin",
        3: "Supervisor",
        4: "Observer",
        5: "Help Desk",
        6: "IoT Service",
        7: "Client Integration",
        8: "Finance"
    },

    # System & Config (5)
    "notification_type": {
        0: "None",
        1: "SMS",
        2: "Email",
        3: "VaIoT"
    },
    "email_source": {
        0: "None",
        1: "Internal",
        2: "External"
    },
    "application_service_type": {
        0: "FITS",
        1: "VTS",
        2: "IMS"
    },
    "company_type": {
        0: "Internal",
        1: "Customer",
        2: "Partner"
    },
    "content_type": {
        0: "Image",
        1: "Video",
        2: "Document",
        3: "Audio"
    },

    # Aliases for backward compatibility
    "status": {
        0: "Pending",
        1: "In Progress",
        2: "Completed",
        3: "Overdue"
    }
}
