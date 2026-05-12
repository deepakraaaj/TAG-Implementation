"""Manual domain hooks for generated domain packages."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_key(value: Any) -> str:
    return str(value or "").strip()


def _flow_candidate_config(config: Dict[str, Any], table: str) -> Dict[str, Any]:
    section = (config or {}).get("flow_candidate_rules")
    if not isinstance(section, dict):
        return {}
    payload = section.get(_normalize_key(table))
    if not isinstance(payload, dict):
        payload = section.get("*")
    return dict(payload) if isinstance(payload, dict) else {}


def _normalized_patterns(raw_patterns: Any) -> List[str]:
    if isinstance(raw_patterns, str):
        pattern = raw_patterns.strip()
        return [pattern] if pattern else []
    if not isinstance(raw_patterns, list):
        return []
    cleaned: List[str] = []
    for item in raw_patterns:
        pattern = _normalize_key(item)
        if pattern:
            cleaned.append(pattern)
    return cleaned


def _matches_any_pattern(message: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns)


def _matches_all_patterns(message: str, patterns: List[str]) -> bool:
    return bool(patterns) and all(re.search(pattern, message, flags=re.IGNORECASE) for pattern in patterns)


def _is_read_query_message(message: str) -> bool:
    text = _normalize_text(message)
    if not text:
        return True
    return bool(
        re.search(
            r"\b(show|list|get|find|view|count|summary|summarize|how many|what|which)\b",
            text,
        )
    )


def is_flow_candidate(message: str, table: str, config: Dict[str, Any] | None = None) -> bool:
    msg = _normalize_text(message)
    if not msg:
        return False

    candidate_cfg = _flow_candidate_config(dict(config or {}), table)
    if not candidate_cfg and _normalize_key(table) == "task_transaction":
        candidate_cfg = {
            "match_any": [
                "\\bcreate\\s+a\\s+maintenance\\s+task\\b",
                "\\bcreate\\s+maintenance\\b",
                "\\bcreate\\s+task\\b",
                "\\bnew\\s+task\\b",
                "\\badd\\s+task\\b",
                "\\bschedule\\s+a\\s+task\\b",
                "\\btasks?\\s+creation\\b",
            ],
            "exclude": [
                "\\bshow\\b",
                "\\blist\\b",
                "\\bcount\\b",
                "\\bsummary\\b",
                "\\bhelp\\b",
            ],
        }

    exclude_patterns = _normalized_patterns(
        candidate_cfg.get("exclude") or candidate_cfg.get("none_patterns")
    )
    if exclude_patterns and _matches_any_pattern(msg, exclude_patterns):
        return False

    match_any_patterns = _normalized_patterns(
        candidate_cfg.get("match_any") or candidate_cfg.get("any_patterns")
    )
    if match_any_patterns and _matches_any_pattern(msg, match_any_patterns):
        return True

    match_all_raw = candidate_cfg.get("match_all") or candidate_cfg.get("all_patterns")
    match_any_all_raw = candidate_cfg.get("match_any_all")

    if (
        not match_any_all_raw
        and isinstance(match_all_raw, list)
        and any(isinstance(item, list) for item in match_all_raw)
    ):
        match_any_all_raw = match_all_raw
        match_all_raw = []

    match_all_patterns = _normalized_patterns(match_all_raw)
    if match_all_patterns and _matches_all_patterns(msg, match_all_patterns):
        return True

    if isinstance(match_any_all_raw, list):
        for group in match_any_all_raw:
            group_patterns = _normalized_patterns(group)
            if group_patterns and _matches_all_patterns(msg, group_patterns):
                return True

    if _normalize_key(table) != "task_transaction":
        return False

    if _is_read_query_message(msg):
        return False

    return bool(
        re.search(r"\b(create|new|add|schedule)\b", msg, flags=re.IGNORECASE)
        and re.search(r"\b(task|tasks|maintenance|work\s*order|workorder)\b", msg, flags=re.IGNORECASE)
    )

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
