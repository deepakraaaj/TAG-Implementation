import re
from typing import Any, Callable, Dict, Iterable, List, Set

from app.assistant.engine.query_policy_service import QueryPolicyService


class FilterProcessorService:
    def __init__(self, policy: QueryPolicyService, parse_kv_pairs: Callable[[str], Dict[str, str]]):
        self.policy = policy
        self.parse_kv_pairs = parse_kv_pairs

    @staticmethod
    def is_placeholder_filter_value(value: Any) -> bool:
        text = str(value or "").strip().lower()
        return text in {"", "null", "none", "undefined", "n/a", "na"}

    @staticmethod
    def is_pure_filter_query(query: str) -> bool:
        text_query = str(query or "").strip()
        if not text_query:
            return False
        if re.fullmatch(
            r"\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^,;]+(\s*[,;]\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^,;]+)*\s*",
            text_query,
            flags=re.IGNORECASE,
        ):
            return True
        if text_query.lower() in {"today", "yesterday", "pending", "completed", "in progress", "overdue"}:
            return True
        return False

    def requests_self_tasks(self, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        terms = self.policy.get_list("self_reference_terms", ["my", "mine", "myself", "me"])
        return any(re.search(rf"\b{re.escape(t)}\b", text_query) for t in terms)

    def requests_all_users(self, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        patterns = self.policy.get_list(
            "all_users_patterns",
            [r"\ball\s+user(s)?\b", r"\ball\s+assignee(s)?\b", r"\bfor\s+everyone\b", r"\beveryone\b"],
        )
        return any(re.search(p, text_query) for p in patterns)

    def mentions_explicit_nonself_user(self, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        if self.requests_all_users(text_query):
            return True
        match = re.search(r"\b(assigned to|assignee|user)\s+([a-zA-Z0-9_]+)\b", text_query, flags=re.IGNORECASE)
        if not match:
            return False
        value = str(match.group(2) or "").strip().lower()
        return value not in {"me", "my", "myself", "mine"}

    def has_task_autorun_context(self, filters: Dict[str, Any]) -> bool:
        normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in (filters or {}).items()}
        if not normalized:
            return False
        user_keys = set(self.policy.get_list("user_filter_keys", ["assigned_user_id", "assignee", "assigned_to", "user"]))
        date_keys = set(self.policy.get_list("date_filter_keys", ["scheduled_date"]))
        facility_keys = set(self.policy.get_list("facility_filter_keys", ["facility_name", "facility_id", "facility"]))
        status_keys = set(self.policy.get_list("status_filter_keys", ["status"]))
        priority_keys = set(self.policy.get_list("priority_filter_keys", ["priority"]))
        has_user = bool(any(normalized.get(k) for k in user_keys))
        has_date = bool(any(normalized.get(k) for k in date_keys))
        has_facility = bool(any(normalized.get(k) for k in facility_keys))
        has_status = bool(any(normalized.get(k) for k in status_keys))
        has_priority = bool(any(normalized.get(k) for k in priority_keys))
        return has_date and (has_user or has_facility or has_status or has_priority)

    def normalized_user_filters(self, intent_filters: Dict | None, query: str) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        source_filters = intent_filters if isinstance(intent_filters, dict) else {}
        query_text = str(query or "")
        user_target_key = self.policy.get_str("user_target_key", "assignee")
        all_user_filter_keys = self.policy.get_list(
            "all_user_filter_keys",
            ["assigned_user_id", "assignee", "assigned_to", "user", "user_id"],
        )
        policy_date_key = self.policy.get_list("date_filter_keys", ["scheduled_date"])[0]
        policy_status_key = self.policy.get_list("status_filter_keys", ["status"])[0]
        status_keywords = self.policy.raw().get("status_keywords")

        for k, v in source_filters.items():
            key = str(k or "").strip()
            value = str(v or "").strip()
            if key and value and not self.is_placeholder_filter_value(value) and value.lower() != key.lower():
                normalized[key] = value

        for k, v in self.parse_kv_pairs(query_text).items():
            key = str(k or "").strip()
            value = str(v or "").strip()
            if key and value and not self.is_placeholder_filter_value(value) and value.lower() != key.lower():
                normalized[key] = value

        lowered = str(query_text or "").lower()
        if not isinstance(status_keywords, dict):
            status_keywords = {
                "pending": "Pending",
                "in progress": "In Progress",
                "in_progress": "In Progress",
                "completed": "Completed",
                "overdue": "Overdue",
                "over due": "Overdue",
            }
        if "today" in lowered:
            normalized.setdefault(policy_date_key, "today")
        if "yesterday" in lowered:
            normalized.setdefault(policy_date_key, "yesterday")
        for k, v in status_keywords.items():
            if str(k).lower() in lowered:
                normalized.setdefault(policy_status_key, str(v))

        task_for_match = re.search(r"\btasks?\s+for\s+([a-zA-Z][a-zA-Z0-9_ ]{0,40})", query_text, re.IGNORECASE)
        if task_for_match:
            candidate = str(task_for_match.group(1) or "").strip()
            candidate = re.split(
                r"\b(today|yesterday|facility|site|location|status|priority|for all users?|for everyone|everyone)\b",
                candidate,
                flags=re.IGNORECASE,
            )[0].strip()
            looks_like_person = bool(re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", candidate))
            if candidate and looks_like_person and candidate.lower() not in {"task", "tasks", "status"}:
                normalized.setdefault(user_target_key, candidate)

        match = re.search(r"\b(assigned to|user|assignee)\s+([a-zA-Z0-9_]+)", query_text, re.IGNORECASE)
        if match:
            val = match.group(2).strip()
            if val.lower() not in {"me", "my", "tasks", "assets", "today", "yesterday"}:
                normalized[user_target_key] = val

        if self.requests_all_users(lowered):
            for key in all_user_filter_keys:
                normalized.pop(key, None)
        return normalized

    def sanitize_prefilled_filters(self, table: str, filters: Dict[str, Any], allowed_columns: Iterable[str]) -> Dict[str, Any]:
        raw = dict(filters or {})
        cleaned: Dict[str, Any] = {}
        ignored_filter_keys = {
            k.lower() for k in self.policy.get_list("ignored_inferred_filter_keys", ["field", "task_assigned"])
        }

        for key, value in raw.items():
            k = str(key or "").strip()
            if not k:
                continue
            if self.is_placeholder_filter_value(value):
                continue
            text_value = str(value or "").strip()
            if not text_value:
                continue
            if text_value.lower() == k.lower():
                continue
            if k.lower() in ignored_filter_keys:
                continue
            cleaned[k] = value

        date_key = self.policy.get_list("date_filter_keys", ["scheduled_date"])[0]
        date_aliases = self.policy.get_list("date_alias_keys", ["date", "due_date"])
        if date_key not in cleaned:
            for alias in date_aliases:
                if alias in cleaned:
                    cleaned[date_key] = cleaned[alias]
                    break
        for alias in date_aliases:
            cleaned.pop(alias, None)

        user_target_key = self.policy.get_str("user_target_key", "assignee")
        user_id_key = self.policy.get_str("user_id_key", "assigned_user_id")
        user_related_keys = set(
            self.policy.get_list("user_filter_keys", [user_id_key, user_target_key, "assigned_to", "user"])
        )
        user_related_keys.update({user_target_key, user_id_key})
        if any(k in cleaned for k in user_related_keys):
            cleaned.pop("name", None)

        allowed = {str(c).strip() for c in (allowed_columns or [])}
        facility_aliases = set(
            self.policy.get_list(
                "facility_input_keys",
                self.policy.get_list("facility_filter_keys", ["facility_name", "facility", "site", "location"]),
            )
        )
        aliases = set(self.policy.get_list("user_input_keys", list(user_related_keys))) | facility_aliases | {
            user_target_key,
            user_id_key,
        }
        return {k: v for k, v in cleaned.items() if k in allowed or k in aliases}

    def looks_like_task_intent(self, query: str, filters: Dict[str, Any]) -> bool:
        text_query = str(query or "").strip().lower()
        terms = self.policy.get_list("task_intent_terms", [r"task", r"tasks", r"work\s*order", r"workorder"])
        if any(re.search(rf"\b{t}\b", text_query) for t in terms):
            return True

        default_task_filter_keys: List[str] = []
        default_task_filter_keys.extend(self.policy.get_list("date_filter_keys", ["scheduled_date"]))
        default_task_filter_keys.extend(self.policy.get_list("status_filter_keys", ["status"]))
        default_task_filter_keys.extend(self.policy.get_list("priority_filter_keys", ["priority"]))
        default_task_filter_keys.extend(
            self.policy.get_list("user_filter_keys", ["assigned_user_id", "assignee", "assigned_to", "user"])
        )
        default_task_filter_keys.extend(
            self.policy.get_list("facility_filter_keys", ["facility_name", "facility_id", "facility"])
        )
        default_task_filter_keys.extend(self.policy.get_list("task_additional_filter_keys", []))
        task_filter_keys = set(self.policy.get_list("task_filter_keys", default_task_filter_keys))
        lowered_keys = {str(k or "").strip().lower() for k in (filters or {}).keys()}
        return bool(lowered_keys & task_filter_keys)
