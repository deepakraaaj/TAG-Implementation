import re
from typing import Dict, Any, List

class MockSQLBuilderNode:
    @classmethod
    def _primary_keywords(cls) -> List[str]:
        return ["task", "tasks", "work order", "workorder", "job", "jobs"]

    @classmethod
    def _user_lookup_filter_keys(cls) -> List[str]:
        return ["assigned_to", "assignee", "user"]

    @classmethod
    def _user_name_filter_key(cls) -> str:
        return "assignee"

    @classmethod
    def _user_id_filter_key(cls) -> str:
        return "user_id"

    @classmethod
    def _date_filter_key(cls) -> str:
        return "scheduled_date"

    @classmethod
    def _status_filter_key(cls) -> str:
        return "status"

    @classmethod
    def _priority_filter_key(cls) -> str:
        return "priority"

    @classmethod
    def _date_phrase_map(cls) -> Dict[str, str]:
        return {"today": "today", "yesterday": "yesterday"}

    @classmethod
    def _status_phrase_map(cls) -> Dict[str, str]:
        return {
            "pending": "Pending",
            "in progress": "In Progress",
            "completed": "Completed",
            "overdue": "Overdue",
        }

    @classmethod
    def _location_filter_keys(cls) -> List[str]:
        return ["facility_name", "facility", "site", "location"]

    @classmethod
    def _all_users_aliases(cls) -> List[str]:
        return ["all users", "all assignees", "for everyone", "everyone"]

    @staticmethod
    def _is_placeholder_filter_value(value: Any) -> bool:
        return str(value or "").strip() == ""

    @classmethod
    def _parse_kv_pairs(cls, query: str) -> Dict[str, str]:
        return {}

    @classmethod
    def _requests_all_users(cls, query: str) -> bool:
        return False

    @classmethod
    def _self_aliases(cls) -> set[str]:
        return {"my", "mine", "myself", "me"}

    @classmethod
    def _normalized_user_filters(cls, intent_filters: Dict, query: str) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        user_name_key = "assignee"
        date_key = "scheduled_date"
        status_key = "status"
        
        # Simulating the rest of the logic
        lowered = str(query or "").lower()
        for phrase, value in cls._date_phrase_map().items():
            if phrase and phrase in lowered:
                normalized.setdefault(date_key, value)
        for phrase, value in cls._status_phrase_map().items():
            if phrase and phrase in lowered:
                normalized.setdefault(status_key, value)

        task_for_match = None
        for keyword in cls._primary_keywords():
            escaped = re.escape(keyword).replace(r"\ ", r"\s+")
            task_for_match = re.search(
                rf"\b{escaped}\b\s+for\s+([a-zA-Z][a-zA-Z0-9_ ]{{0,40}})",
                query,
                re.IGNORECASE,
            )
            if task_for_match:
                print(f"Matched keyword: {keyword}")
                break
        
        if task_for_match:
            candidate = str(task_for_match.group(1) or "").strip()
            print(f"Captured candidate: {candidate}")
            location_terms = [str(k).strip().replace("_", " ") for k in cls._location_filter_keys() if str(k).strip()]
            date_terms = [str(k).strip() for k in cls._date_phrase_map().keys()]
            status_terms = [str(k).strip() for k in cls._status_phrase_map().keys()]
            split_terms = (
                ["status", "priority", "for all users?", "for everyone", "everyone"]
                + date_terms
                + status_terms
                + location_terms
            )
            split_pattern = "|".join(sorted({re.escape(term) for term in split_terms if term}, key=len, reverse=True))
            candidate_split = re.split(
                rf"\b({split_pattern})\b" if split_pattern else r"\b(status|priority)\b",
                candidate,
                flags=re.IGNORECASE,
            )
            print(f"Candidate split chunks: {candidate_split}")
            candidate = candidate_split[0].strip()
            print(f"Candidate after split: {candidate}")
            looks_like_person = bool(re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", candidate))
            excluded_keywords = {str(item).strip().lower() for item in cls._primary_keywords()}
            if candidate and looks_like_person and candidate.lower() not in (excluded_keywords | {status_key.lower()}):
                normalized.setdefault(user_name_key, candidate)
        
        return normalized

query = "show pending tasks for Nirmala"
print(f"Query: {query}")
filters = MockSQLBuilderNode._normalized_user_filters({}, query)
print(f"Resulting filters: {filters}")
