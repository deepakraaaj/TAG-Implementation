FIELD_LABELS = {
    # Task Transaction Fields
    "id": "Task ID",
    "task_id": "Task Identifier",
    "task_description_id": "Task Template",
    "scheduled_date": "Scheduled Date",
    "actual_date_time": "Actual Completion Date",
    "status": "Status",
    "priority": "Priority",
    "facility_id": "Facility/Location",
    "asset_id": "Asset/Equipment",
    "assigned_user_id": "Assigned To",
    "assigned_from_id": "Assigned By",
    "closed_by": "Completed By",
    "closed_time": "Completion Time",
    "date_created": "Created Date",
    "date_updated": "Last Updated",
    "remarks": "Notes/Remarks",
    "file_path": "After Photo",
    "before_file_path": "Before Photo",
    "schedule_id": "Schedule",
    "scheduler_task_details_id": "Schedule Details",
    "maintenance_transaction_id": "Maintenance Job",
    "location_level_id": "Location",
    "is_active": "Active",
    "company_id": "Company",

    # Facility Fields
    "facility_name": "Facility Name",
    "facility_code": "Facility Code",
    "facility_status": "Facility Status",
    "facility_type": "Facility Type",
    "location_id": "Location",

    # Asset Fields
    "asset_name": "Asset Name",
    "asset_code": "Asset Code",
    "asset_category": "Asset Category",
    "asset_type": "Asset Type",
    "asset_scan_frequency": "Scan Frequency",

    # Scheduler Fields
    "scheduler_name": "Schedule Name",
    "scheduled_date_time": "Scheduled Date/Time",
    "is_open": "Active Schedule",
    "frequency": "Recurrence",
    "next_occurrence": "Next Occurrence",

    # Check List Fields
    "check_list_id": "Checklist Template",
    "check_list_status": "Checklist Status",
    "checklist_name": "Checklist Name",

    # User Fields
    "user_id": "User",
    "user_name": "User Name",
    "first_name": "First Name",
    "last_name": "Last Name",
    "email": "Email",
    "phone": "Phone",
    "role_type": "Role",

    # Maintenance/Transaction Fields
    "transaction_type": "Transaction Type",
    "recording_mode": "Recording Mode",
    "process_type": "Process Type",
    "process_status": "Process Status",
    "maintenance_date": "Maintenance Date",

    # Notification Fields
    "notification_type": "Notification Type",
    "email_source": "Email Source",

    # Common Fields
    "name": "Name",
    "code": "Code",
    "description": "Description",
    "type": "Type",
    "created_by": "Created By",
    "updated_by": "Updated By",
}

FIELD_OPTIONS = {
    "status": [
        {"value": 0, "label": "Pending"},
        {"value": 1, "label": "In Progress"},
        {"value": 2, "label": "Completed"},
        {"value": 3, "label": "Overdue"}
    ],
    "priority": [
        {"value": 0, "label": "Low"},
        {"value": 1, "label": "Medium"},
        {"value": 2, "label": "High"},
        {"value": 3, "label": "Critical"}
    ],
    "facility_status": [
        {"value": 0, "label": "Assigned"},
        {"value": 1, "label": "In Progress"},
        {"value": 2, "label": "Overdue"},
        {"value": 3, "label": "Delay In Progress"},
        {"value": 4, "label": "Completed"}
    ],
    "check_list_status": [
        {"value": 0, "label": "Pending"},
        {"value": 1, "label": "In Progress"},
        {"value": 2, "label": "Completed"},
        {"value": 3, "label": "Overdue"}
    ],
    "process_status": [
        {"value": 0, "label": "New"},
        {"value": 1, "label": "Proceeded"}
    ],
    "transaction_type": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "Record"},
        {"value": 2, "label": "Mapping"},
        {"value": 3, "label": "Audit"}
    ],
    "recording_mode": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "Manual"},
        {"value": 2, "label": "Automatic"}
    ],
    "asset_scan_frequency": [
        {"value": 0, "label": "Never"},
        {"value": 1, "label": "Daily"},
        {"value": 7, "label": "Weekly"},
        {"value": 30, "label": "Monthly"},
        {"value": 90, "label": "Quarterly"},
        {"value": 365, "label": "Yearly"}
    ],
    "day_of_week": [
        {"value": 1, "label": "Monday"},
        {"value": 2, "label": "Tuesday"},
        {"value": 3, "label": "Wednesday"},
        {"value": 4, "label": "Thursday"},
        {"value": 5, "label": "Friday"},
        {"value": 6, "label": "Saturday"},
        {"value": 7, "label": "Sunday"}
    ],
    "exam_session_type": [
        {"value": 0, "label": "Morning"},
        {"value": 1, "label": "Afternoon"},
        {"value": 2, "label": "Evening"}
    ],
    "entity_type": [
        {"value": 0, "label": "Asset"},
        {"value": 1, "label": "Facility"},
        {"value": 2, "label": "Location"}
    ],
    "role_type": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "Super Admin"},
        {"value": 2, "label": "Admin"},
        {"value": 3, "label": "Supervisor"},
        {"value": 4, "label": "Observer"},
        {"value": 5, "label": "Help Desk"},
        {"value": 6, "label": "IoT Service"},
        {"value": 7, "label": "Client Integration"},
        {"value": 8, "label": "Finance"}
    ],
    "notification_type": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "SMS"},
        {"value": 2, "label": "Email"},
        {"value": 3, "label": "VaIoT"}
    ],
    "email_source": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "Internal"},
        {"value": 2, "label": "External"}
    ],
    "application_service_type": [
        {"value": 0, "label": "FITS"},
        {"value": 1, "label": "VTS"},
        {"value": 2, "label": "IMS"}
    ],
    "company_type": [
        {"value": 0, "label": "Internal"},
        {"value": 1, "label": "Customer"},
        {"value": 2, "label": "Partner"}
    ],
    "content_type": [
        {"value": 0, "label": "Image"},
        {"value": 1, "label": "Video"},
        {"value": 2, "label": "Document"},
        {"value": 3, "label": "Audio"}
    ],
    "is_active": [
        {"value": 1, "label": "Yes"},
        {"value": 0, "label": "No"}
    ],
    "is_open": [
        {"value": 1, "label": "Open"},
        {"value": 0, "label": "Closed"}
    ]
}

LOOKUP_CONFIGS = {
    "facility_id": {
        "table": "facility",
        "key_column": "id",
        "label_column": "name",
        "description_column": "code",
        "filter_column": "is_active",
        "filter_value": 1
    },
    "asset_id": {
        "table": "asset",
        "key_column": "id",
        "label_column": "name",
        "description_column": "code",
        "filter_column": "is_active",
        "filter_value": 1
    },
    "assigned_user_id": {
        "table": "user",
        "key_column": "id",
        "label_column": "first_name",
        "description_column": "email",
        "concat_display": "{first_name} {last_name}",
        "filter_column": "is_active",
        "filter_value": 1
    },
    "assigned_from_id": {
        "table": "user",
        "key_column": "id",
        "label_column": "first_name",
        "description_column": "email",
        "concat_display": "{first_name} {last_name}",
        "filter_column": "is_active",
        "filter_value": 1
    },
    "closed_by": {
        "table": "user",
        "key_column": "id",
        "label_column": "first_name",
        "description_column": "email",
        "concat_display": "{first_name} {last_name}",
        "filter_column": "is_active",
        "filter_value": 1
    },
    "schedule_id": {
        "table": "scheduler",
        "key_column": "id",
        "label_column": "name",
        "description_column": "description"
    },
    "task_description_id": {
        "table": "task_description",
        "key_column": "id",
        "label_column": "name",
        "description_column": "description"
    },
    "location_level_id": {
        "table": "location_hierarchy_master",
        "key_column": "id",
        "label_column": "name",
        "description_column": "level_name"
    },
    "check_list_id": {
        "table": "check_list_master",
        "key_column": "id",
        "label_column": "name",
        "description_column": "description"
    }
}
