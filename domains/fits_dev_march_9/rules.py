"""Manual domain hooks for generated domain packages."""

# Validation Rules
VALIDATION_RULES = {
    "task_transaction": {
        "status": {
            "rule": "status_in_range",
            "min": 0,
            "max": 3,
            "error": "Status must be between 0 (Pending) and 3 (Overdue)"
        },
        "priority": {
            "rule": "priority_in_range",
            "min": 0,
            "max": 3,
            "error": "Priority must be between 0 (Low) and 3 (Critical)"
        },
        "scheduled_date": {
            "rule": "required_for_task",
            "error": "Scheduled date is required for task creation"
        },
        "facility_id": {
            "rule": "foreign_key_exists",
            "table": "facility",
            "column": "id",
            "error": "Facility must exist in the system"
        },
        "assigned_user_id": {
            "rule": "foreign_key_exists",
            "table": "user",
            "column": "id",
            "error": "Assigned user must exist in the system"
        }
    }
}

# Status Transition Rules
STATUS_TRANSITIONS = {
    "task_transaction": {
        "allowed_transitions": {
            0: [0, 1, 3],  # From Pending: can go to Pending, In Progress, Overdue
            1: [1, 2, 3],  # From In Progress: can go to In Progress, Completed, Overdue
            2: [2],        # From Completed: can only stay Completed
            3: [3, 2]      # From Overdue: can go to Overdue or Completed
        },
        "completion_requirements": {
            2: ["closed_by", "closed_time"]  # When status=Completed, require who closed it and when
        }
    }
}

# Mutability Rules
MUTATION_RULES = {
    "immutable_fields": {
        "task_transaction": ["id", "task_id", "date_created"]
    },
    "create_required_fields": {
        "task_transaction": ["task_description_id", "scheduled_date", "facility_id", "priority"]
    },
    "update_forbidden_fields": {
        "task_transaction": ["id", "task_id", "date_created", "company_id"]
    }
}

# Business Logic Hooks
BUSINESS_HOOKS = {
    "before_create": {
        "task_transaction": [
            "validate_scheduled_date_not_past",
            "validate_facility_is_active",
            "validate_asset_is_available",
            "set_default_status_to_pending"
        ]
    },
    "before_update": {
        "task_transaction": [
            "validate_status_transition",
            "validate_completion_fields_when_done",
            "prevent_past_date_updates"
        ]
    },
    "after_update": {
        "task_transaction": [
            "log_status_change",
            "trigger_completion_notification",
            "update_facility_workload_cache"
        ]
    }
}

# SLA and Priority Mappings
SLA_MAPPINGS = {
    "priority": {
        0: {"label": "Low", "sla_days": 30, "escalation_days": 35},
        1: {"label": "Medium", "sla_days": 14, "escalation_days": 16},
        2: {"label": "High", "sla_days": 7, "escalation_days": 8},
        3: {"label": "Critical", "sla_days": 1, "escalation_days": 2}
    }
}

# Filter Scope Rules
FILTER_SCOPE = {
    "facility_scope": {
        "table": "task_transaction",
        "column": "facility_id",
        "rule": "user_must_have_facility_access"
    },
    "user_scope": {
        "table": "task_transaction",
        "column": "assigned_user_id",
        "rule": "can_only_view_own_or_supervised_tasks"
    }
}

# Default Value Mappings
DEFAULT_VALUES = {
    "task_transaction": {
        "status": 0,  # Pending
        "priority": 1,  # Medium
        "is_active": 1,  # Active
        "date_created": "NOW()",
        "date_updated": "NOW()"
    },
    "facility": {
        "is_active": 1
    },
    "asset": {
        "is_active": 1
    }
}
