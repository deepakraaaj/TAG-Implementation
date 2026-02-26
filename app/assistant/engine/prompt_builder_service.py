import logging
from typing import Any, Dict, List, Tuple

from app.assistant.engine.query_policy_service import QueryPolicyService

logger = logging.getLogger(__name__)


class PromptBuilderService:
    def __init__(self, policy: QueryPolicyService, catalog: Any):
        self.policy = policy
        self.catalog = catalog

    @staticmethod
    def compact_label_options(options: List[Tuple[str, str]], limit: int = 6) -> List[Dict[str, str]]:
        return [{"label": label, "value": value} for label, value in options[:limit]]

    @staticmethod
    def filter_options_excluding_prefilled(
        options: List[Dict[str, str]],
        prefilled_filters: Dict[str, Any] | None = None,
    ) -> List[Dict[str, str]]:
        prefilled = {str(k or "").strip().lower() for k in (prefilled_filters or {}).keys() if str(k or "").strip()}
        if not prefilled:
            return list(options or [])

        filtered: List[Dict[str, str]] = []
        for opt in options or []:
            value = str((opt or {}).get("value", "")).strip()
            if "=" in value:
                field = str(value.split("=", 1)[0]).strip().lower()
                if field and field in prefilled:
                    continue
            filtered.append(opt)
        return filtered

    def candidate_filter_columns(self, table: str, limit: int = 6) -> List[str]:
        blocked = self.policy.system_columns()
        cols = self.catalog.important_columns(table) or []
        return [c for c in sorted(cols) if c not in blocked][:limit]

    def policy_menu_options(self) -> List[Dict[str, str]]:
        value = self.policy.raw().get("task_menu_options")
        if isinstance(value, list):
            out: List[Dict[str, str]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "")).strip()
                val = str(item.get("value", "")).strip()
                if label and val:
                    out.append({"label": label, "value": val})
            if out:
                return out
        date_key = self.policy.get_list("date_filter_keys", ["scheduled_date"])[0]
        current_user_filter_key = self.policy.get_str("current_user_filter_key", "assigned_to")
        user_key = self.policy.get_str("user_target_key", "assignee")
        status_key = self.policy.get_list("status_filter_keys", ["status"])[0]
        priority_key = self.policy.get_list("priority_filter_keys", ["priority"])[0]
        current_user_alias = self.policy.get_str("current_user_alias", "current_user")
        return [
            {"label": "Today (your tasks)", "value": f"{date_key}=today, {current_user_filter_key}={current_user_alias}"},
            {"label": "Yesterday", "value": f"{date_key}=yesterday"},
            {"label": "Pick a date (YYYY-MM-DD)", "value": f"{date_key}="},
            {"label": "Different user / assignee", "value": f"{user_key}="},
            {"label": "Status", "value": f"{status_key}="},
            {"label": "Priority", "value": f"{priority_key}="},
        ]

    def generate_dynamic_filter_options(self, table: str) -> List[Dict[str, str]]:
        try:
            table_info = self.catalog.table_meta(table)
            columns_dict = table_info.get("important_columns", {})
            if not columns_dict:
                logger.warning("No columns found for table %s", table)
                return [{"label": "Type your filters manually", "value": ""}]

            options: List[Dict[str, str]] = []
            seen_labels = set()
            label_cfg = self.policy.raw().get("dynamic_filter_label_preferences")
            label_preferences: Dict[str, List[str]] = {}
            if isinstance(label_cfg, dict):
                for label, terms in label_cfg.items():
                    if not str(label or "").strip() or not isinstance(terms, list):
                        continue
                    normalized_terms = [str(t).strip().lower() for t in terms if str(t).strip()]
                    if normalized_terms:
                        label_preferences[str(label).strip()] = normalized_terms
            if not label_preferences:
                label_preferences = {
                    "Scheduled date": ["scheduled", "due", "date"],
                    "Assigned to": ["assign", "user", "created_by"],
                    "Location": ["facility", "location", "site"],
                    "Status": ["status"],
                    "Priority": ["priority"],
                    "Task/name": ["name", "title", "code"],
                }

            skip_defaults = list(self.policy.system_columns()) + ["active_date", "active_time", "closed_time", "is_active"]
            skip_columns = {
                str(c).strip().lower()
                for c in self.policy.get_list("dynamic_filter_skip_columns", skip_defaults)
            }

            for label, substrings in label_preferences.items():
                best_col = None
                for sub in substrings:
                    for col_name in columns_dict.keys():
                        col_lower = col_name.lower()
                        if col_lower in skip_columns:
                            continue
                        if sub in col_lower:
                            best_col = col_name
                            break
                    if best_col:
                        break
                if best_col and label not in seen_labels:
                    options.append({"label": label, "value": f"{best_col}="})
                    seen_labels.add(label)

            has_date = any("date" in c.lower() or "time" in c.lower() for c in columns_dict.keys())
            if has_date:
                options.insert(0, {"label": "Yesterday", "value": "yesterday"})
                options.insert(0, {"label": "Today", "value": "today"})
            return options[:6]
        except Exception as exc:
            logger.error("Failed to generate dynamic filters for %s: %s", table, exc, exc_info=True)
            return [{"label": "Type your filters manually", "value": ""}]

    def build_filter_prompt_payload(
        self,
        table: str,
        suggested_fields: List[str],
        prefilled_filters: Dict[str, Any] | None = None,
        options_override: List[Dict[str, str]] | None = None,
    ) -> Dict[str, Any]:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        if not fields:
            fields = self.policy.get_list("default_prompt_fields", ["id", "name"])

        dynamic_options = options_override or self.generate_dynamic_filter_options(table)
        example = "id=123, name=example"
        if dynamic_options:
            first_val = dynamic_options[0]["value"]
            if "=" in first_val:
                example = f"{first_val}, {fields[0] if fields else 'id'}=value"

        return {
            "workflow_id": "select_filters",
            "state": "collect_filters",
            "completed": False,
            "mode": "menu",
            "next_field": "filters",
            "collected_data": {
                "operation": "select",
                "table": table,
                "required_fields": ["filters"],
                "collected_fields": dict(prefilled_filters or {}),
            },
            "ui": {
                "type": "menu",
                "title": f"Add filters for {table}",
                "options": dynamic_options,
                "suggested_fields": fields[:6],
                "example": example,
            },
        }

    def build_filter_prompt_message(
        self,
        table: str,
        prefilled_filters: Dict[str, Any] | None = None,
        focus_date: bool = False,
        options_override: List[Dict[str, str]] | None = None,
    ) -> str:
        dynamic_options = options_override or self.generate_dynamic_filter_options(table)
        if focus_date:
            lines = [f"Choose a date to check `{table}` status."]
        else:
            lines = [f"Let me help you narrow down `{table}`."]
        lines.append("Pick an option number/value, or type filters directly. Use `back`/`cancel` anytime.")
        if prefilled_filters:
            user_id_key = self.policy.get_str("user_id_key", "assigned_user_id")
            if prefilled_filters.get(user_id_key):
                lines.append(f"Defaulting to your assigned tasks. Type `{user_id_key}=<id>` to change.")

        option_fields = [opt["value"].split("=")[0] for opt in dynamic_options if "=" in opt["value"]]
        status_key = self.policy.get_list("status_filter_keys", ["status"])[0]
        date_key = self.policy.get_list("date_filter_keys", ["scheduled_date"])[0]
        if status_key in option_fields:
            example = f"{status_key}=Completed, {date_key}=2025-07-18"
        elif "is_active" in option_fields:
            example = "is_active=1, name=example"
        else:
            example = "id=123, name=example"

        lines.append(f"Example: {example}")
        return "\n".join(lines)
