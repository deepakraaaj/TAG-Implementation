from typing import Dict, Any, List, Tuple
import re
import logging
import difflib

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp
from sqlalchemy import text

from app.assistant.services.sql_builder_service import SQLBuilderService
from app.assistant.services.prompt_injection_detector import PromptInjectionDetector
from app.assistant.services.intent_detection_service import IntentDetectionService
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)


class SQLBuilderNode:
    def __init__(self):
        self.sql_builder = SQLBuilderService()
        self.injection_detector = PromptInjectionDetector()
        self.intent_detector = IntentDetectionService()
        self.schema = SchemaService()

    @staticmethod
    def _looks_like_sql_statement(query: str) -> bool:
        text_query = str(query or "").strip()
        return bool(re.match(r"^(SELECT|INSERT|UPDATE)\b", text_query, flags=re.IGNORECASE))

    @staticmethod
    def _is_placeholder_filter_value(value: Any) -> bool:
        text = str(value or "").strip().lower()
        return text in {"", "null", "none", "undefined", "n/a", "na"}

    def _extract_forced_table_from_query(self, query: str) -> str:
        text_query = str(query or "").strip()
        match = re.match(r"^\s*show\s+([A-Za-z_][A-Za-z0-9_]*)\b", text_query, flags=re.IGNORECASE)
        if not match:
            return ""
        candidate = str(match.group(1) or "").strip()
        if not candidate:
            return ""
        table_names = set(self.sql_builder.catalog.table_names() or [])
        return candidate if candidate in table_names else ""

    def _query_mentions_explicit_table(self, query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        for table_name in set(self.sql_builder.catalog.table_names() or []):
            t = str(table_name or "").strip().lower()
            if t and re.search(rf"\b{re.escape(t)}\b", text_query):
                return True
        return False

    def _is_explicit_list_request(self, query: str, resolved_table: str = "") -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        if not re.match(r"^(list|show|get|find)\b", text_query):
            return False
        if str(resolved_table or "").strip():
            return True
        return self._query_mentions_explicit_table(query)

    @staticmethod
    def _is_pure_filter_query(query: str) -> bool:
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

    @staticmethod
    def _looks_like_task_intent(query: str, filters: Dict[str, Any]) -> bool:
        text_query = str(query or "").strip().lower()
        if re.search(r"\b(task|tasks|work\s*order|workorder)\b", text_query):
            return True

        task_filter_keys = {
            "scheduled_date",
            "status",
            "priority",
            "assigned_user_id",
            "assigned_to",
            "assignee",
            "facility_name",
            "location_level_id",
        }
        lowered_keys = {str(k or "").strip().lower() for k in (filters or {}).keys()}
        return bool(lowered_keys & task_filter_keys)

    @staticmethod
    def _mentions_explicit_nonself_user(query: str) -> bool:
        text_query = str(query or "").strip().lower()
        if not text_query:
            return False
        if SQLBuilderNode._requests_all_users(text_query):
            return True
        match = re.search(r"\b(assigned to|assignee|user)\s+([a-zA-Z0-9_]+)\b", text_query, flags=re.IGNORECASE)
        if not match:
            return False
        value = str(match.group(2) or "").strip().lower()
        return value not in {"me", "my", "myself", "mine"}

    @staticmethod
    def _requests_self_tasks(query: str) -> bool:
        text_query = str(query or "").strip().lower()
        return bool(re.search(r"\b(my|mine|myself|me)\b", text_query))

    @staticmethod
    def _requests_all_users(query: str) -> bool:
        text_query = str(query or "").strip().lower()
        patterns = [
            r"\ball\s+user(s)?\b",
            r"\ball\s+assignee(s)?\b",
            r"\bfor\s+everyone\b",
            r"\beveryone\b",
        ]
        return any(re.search(p, text_query) for p in patterns)

    @staticmethod
    def _has_task_autorun_context(filters: Dict[str, Any]) -> bool:
        normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in (filters or {}).items()}
        if not normalized:
            return False
        has_user = bool(
            normalized.get("assigned_user_id")
            or normalized.get("assignee")
            or normalized.get("assigned_to")
            or normalized.get("user")
        )
        has_date = bool(normalized.get("scheduled_date"))
        has_facility = bool(normalized.get("facility_name") or normalized.get("facility_id") or normalized.get("facility"))
        has_status = bool(normalized.get("status"))
        has_priority = bool(normalized.get("priority"))
        # Consider task query specific enough when date is present plus at least one strong narrowing filter.
        return has_date and (has_user or has_facility or has_status or has_priority)

    @staticmethod
    def _is_unfiltered_select(sql: str) -> bool:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return False
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return False
        if not isinstance(parsed, exp.Select):
            return False
        return parsed.args.get("where") is None

    @staticmethod
    def _select_where_columns(sql: str) -> set[str]:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return set()
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return set()
        if not isinstance(parsed, exp.Select):
            return set()
        where_expr = parsed.args.get("where")
        if where_expr is None:
            return set()
        cols = set()
        for col in where_expr.find_all(exp.Column):
            name = str(col.name or "").strip().lower()
            if name:
                cols.add(name)
        return cols

    @staticmethod
    def _normalized_user_filters(intent_filters: Dict, query: str) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if isinstance(intent_filters, dict):
            for k, v in intent_filters.items():
                key = str(k or "").strip()
                value = str(v or "").strip()
                if (
                    key
                    and value
                    and not SQLBuilderNode._is_placeholder_filter_value(value)
                    and value.lower() != key.lower()
                ):
                    normalized[key] = value
        for k, v in SQLBuilderService.parse_kv_pairs(query).items():
            key = str(k or "").strip()
            value = str(v or "").strip()
            if (
                key
                and value
                and not SQLBuilderNode._is_placeholder_filter_value(value)
                and value.lower() != key.lower()
            ):
                normalized[key] = value
        lowered = str(query or "").lower()
        if "today" in lowered:
            normalized.setdefault("scheduled_date", "today")
        if "yesterday" in lowered:
            normalized.setdefault("scheduled_date", "yesterday")
        if "pending" in lowered:
            normalized.setdefault("status", "Pending")
        if "in progress" in lowered or "in_progress" in lowered:
            normalized.setdefault("status", "In Progress")
        if "completed" in lowered:
            normalized.setdefault("status", "Completed")
        if "overdue" in lowered or "over due" in lowered:
            normalized.setdefault("status", "Overdue")

        task_for_match = re.search(r"\btasks?\s+for\s+([a-zA-Z][a-zA-Z0-9_ ]{0,40})", query, re.IGNORECASE)
        if task_for_match:
            candidate = str(task_for_match.group(1) or "").strip()
            candidate = re.split(
                r"\b(today|yesterday|facility|site|location|status|priority|for all users?|for everyone|everyone)\b",
                candidate,
                flags=re.IGNORECASE,
            )[0].strip()
            looks_like_person = bool(re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,2}", candidate))
            if candidate and looks_like_person and candidate.lower() not in {"task", "tasks", "status"}:
                normalized.setdefault("assignee", candidate)
        
        # Regex extraction for common user patterns
        match = re.search(r"\b(assigned to|user|assignee)\s+([a-zA-Z0-9_]+)", query, re.IGNORECASE)
        if match:
             val = match.group(2).strip()
             if val.lower() not in {"me", "my", "tasks", "assets", "today", "yesterday"}:
                  normalized["assignee"] = val
        if SQLBuilderNode._requests_all_users(lowered):
            for key in ("assigned_user_id", "assignee", "assigned_to", "user", "user_id"):
                normalized.pop(key, None)
        return normalized

    def _generate_dynamic_filter_options(self, table: str) -> list[Dict[str, str]]:
        """Generate simplified question-based filter options (When, What, Who, Where)."""
        try:
            catalog = self.sql_builder.catalog
            table_info = catalog.table_meta(table)
            columns_dict = table_info.get("important_columns", {})
            
            if not columns_dict:
                logger.warning(f"No columns found for table {table}")
                return [{"label": "Type your filters manually", "value": ""}]
            
            options = []
            seen_labels = set()
            
            # Define priority columns for labels to ensure we pick the best one
            # Maps Label -> list of column substrings to match, in order of preference
            label_preferences = {
                "Scheduled date": ["scheduled", "due", "date"],
                "Assigned to": ["assign", "user", "created_by"],
                "Location": ["facility", "location", "site"],
                "Status": ["status"],
                "Priority": ["priority"],
                "Task/name": ["name", "title", "code"],
            }
            
            # We construct a tailored list
            for label, substrings in label_preferences.items():
                # Find best column for this label
                best_col = None
                for sub in substrings:
                    for col_name in columns_dict.keys():
                        col_lower = col_name.lower()
                        # Skip technical fields
                        if col_lower in {
                            "id",
                            "company_id",
                            "date_updated",
                            "updated_by",
                            "created_by",
                            "date_created",
                            "active_date",
                            "active_time",
                            "closed_time",
                            "is_active",
                        }:
                            continue
                            
                        if sub in col_lower:
                            # Check if we already used this column for another label?
                            # Not strictly necessary if our labels are distinct, but good practice.
                            best_col = col_name
                            break
                    if best_col:
                        break
                
                if best_col and label not in seen_labels:
                    options.append({"label": label, "value": f"{best_col}="})
                    seen_labels.add(label)
            
            # Check for date shortcuts
            has_date = any("date" in c.lower() or "time" in c.lower() for c in columns_dict.keys())
            if has_date:
                # Add shortcuts at the TOP
                options.insert(0, {"label": "Yesterday", "value": "yesterday"})
                options.insert(0, {"label": "Today", "value": "today"})
                
            return options[:6]
            
        except Exception as e:
            logger.error(f"Failed to generate dynamic filters for {table}: {e}", exc_info=True)
            return [{"label": "Type your filters manually", "value": ""}]

    def _sanitize_prefilled_filters(self, table: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(filters or {})
        cleaned: Dict[str, Any] = {}

        for key, value in raw.items():
            k = str(key or "").strip()
            if not k:
                continue
            if self._is_placeholder_filter_value(value):
                continue
            text_value = str(value or "").strip()
            if not text_value:
                continue
            if text_value.lower() == k.lower():
                continue
            # Drop noisy meta-like inferences from intent detector.
            if k.lower() in {"field", "task_assigned"}:
                continue
            cleaned[k] = value

        # Normalize common date aliases to one key for cleaner UI.
        if "scheduled_date" not in cleaned:
            for alias in ("date", "due_date"):
                if alias in cleaned:
                    cleaned["scheduled_date"] = cleaned[alias]
                    break
        cleaned.pop("date", None)
        cleaned.pop("due_date", None)

        # When assignee is already inferred, plain name often duplicates it.
        if any(k in cleaned for k in {"assignee", "assigned_to", "assigned_user_id"}):
            cleaned.pop("name", None)

        # Keep only known/allowed fields for the target table plus supported aliases.
        allowed = {str(c).strip() for c in self.sql_builder.catalog.important_columns(table)}
        aliases = {"assignee", "assigned_to", "user", "facility", "facility_name", "site", "location"}
        return {k: v for k, v in cleaned.items() if k in allowed or k in aliases}

    @staticmethod
    def _compact_label_options(options: List[Tuple[str, str]], limit: int = 6) -> List[Dict[str, str]]:
        return [{"label": label, "value": value} for label, value in options[:limit]]

    def _lookup_facility_candidates(self, value: str, metadata: Dict[str, Any]) -> List[str]:
        query_value = str(value or "").strip()
        if not query_value:
            return []
        db_url = (metadata or {}).get("db_connection_string")
        engine = self.schema.get_engine_for_url(db_url)
        company_id = (metadata or {}).get("company_id")
        names: List[str] = []
        used_fuzzy = False
        with engine.connect() as conn:
            if company_id:
                rows = conn.execute(
                    text(
                        "SELECT name FROM facility "
                        "WHERE company_id = :company_id AND LOWER(name) LIKE :q "
                        "ORDER BY name LIMIT 12"
                    ),
                    {"company_id": company_id, "q": f"%{query_value.lower()}%"},
                ).mappings().all()
                names = [str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()]
            if not names:
                params = {"q": f"%{query_value.lower()}%"}
                where = "WHERE LOWER(name) LIKE :q"
                if company_id:
                    where = "WHERE company_id = :company_id"
                    params["company_id"] = company_id
                rows = conn.execute(text(f"SELECT name FROM facility {where} ORDER BY name LIMIT 200"), params).mappings().all()
                all_names = [str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()]
                names = difflib.get_close_matches(query_value, all_names, n=6, cutoff=0.60)
                if len(names) == 1:
                    ratio = difflib.SequenceMatcher(None, query_value.lower(), names[0].lower()).ratio()
                    if ratio < 0.72:
                        names = []
                used_fuzzy = True
        unique = []
        seen = set()
        for n in names:
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(n)
        setattr(self, "_last_facility_lookup_used_fuzzy", used_fuzzy)
        return unique

    def _lookup_user_candidates(self, value: str, metadata: Dict[str, Any]) -> List[Tuple[str, str]]:
        query_value = str(value or "").strip()
        if not query_value:
            return []
        db_url = (metadata or {}).get("db_connection_string")
        engine = self.schema.get_engine_for_url(db_url)
        query_lower = query_value.lower()
        used_fuzzy = False
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, first_name, last_name FROM `user` "
                    "WHERE LOWER(first_name) LIKE :q OR LOWER(last_name) LIKE :q "
                    "ORDER BY first_name, last_name LIMIT 12"
                ),
                {"q": f"%{query_lower}%"},
            ).mappings().all()
            options: List[Tuple[str, str]] = []
            for r in rows:
                first = str(r.get("first_name", "")).strip()
                last = str(r.get("last_name", "")).strip()
                if not first and not last:
                    continue
                full = f"{first} {last}".strip()
                if not full:
                    continue
                options.append((full, f"assignee={full}"))
            if options:
                setattr(self, "_last_user_lookup_used_fuzzy", used_fuzzy)
                return options

            rows = conn.execute(text("SELECT id, first_name, last_name FROM `user` ORDER BY first_name, last_name LIMIT 300")).mappings().all()
            all_users = []
            for r in rows:
                first = str(r.get("first_name", "")).strip()
                last = str(r.get("last_name", "")).strip()
                full = f"{first} {last}".strip()
                if full:
                    all_users.append(full)
            close_names = difflib.get_close_matches(query_value, all_users, n=6, cutoff=0.60)
            if len(close_names) == 1:
                ratio = difflib.SequenceMatcher(None, query_value.lower(), close_names[0].lower()).ratio()
                if ratio < 0.72:
                    close_names = []
            used_fuzzy = True
            result = [(name, f"assignee={name}") for name in all_users if name in close_names]
            setattr(self, "_last_user_lookup_used_fuzzy", used_fuzzy)
            return result

    def _fallback_facility_options(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        db_url = (metadata or {}).get("db_connection_string")
        engine = self.schema.get_engine_for_url(db_url)
        company_id = (metadata or {}).get("company_id")
        query_sql = "SELECT name FROM facility ORDER BY name LIMIT 6"
        params: Dict[str, Any] = {}
        if company_id:
            query_sql = "SELECT name FROM facility WHERE company_id = :company_id ORDER BY name LIMIT 6"
            params["company_id"] = company_id
        with engine.connect() as conn:
            rows = conn.execute(text(query_sql), params).mappings().all()
        names = [str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()]
        return self._compact_label_options([(n, f"facility_name={n}") for n in names])

    def _fallback_user_options(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        db_url = (metadata or {}).get("db_connection_string")
        engine = self.schema.get_engine_for_url(db_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, first_name, last_name FROM `user` ORDER BY first_name, last_name LIMIT 6")
            ).mappings().all()
        opts: List[Tuple[str, str]] = []
        for r in rows:
            first = str(r.get("first_name", "")).strip()
            last = str(r.get("last_name", "")).strip()
            full = f"{first} {last}".strip()
            if full:
                opts.append((full, f"assignee={full}"))
        return self._compact_label_options(opts)

    def _build_disambiguation_prompt(
        self,
        table: str,
        explicit_filters: Dict[str, Any],
        target_field: str,
        options: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        count = len(options or [])
        if count <= 1:
            message = f"I found a close match for `{target_field}`. Please confirm this option."
        else:
            message = f"I found multiple matches for `{target_field}`. Please pick one option."
        candidate_filters = [
            c
            for c in sorted(self.sql_builder.catalog.important_columns(table))
            if c not in {"id", "company_id", "created_by", "updated_by", "date_created", "date_updated"}
        ][:6]
        payload = self._filter_prompt_payload(
            table,
            candidate_filters or [target_field],
            prefilled_filters=self._sanitize_prefilled_filters(table, explicit_filters),
            options_override=options,
        )
        payload_ui = payload.get("ui") or {}
        payload_ui["title"] = f"Choose {target_field}"
        payload["ui"] = payload_ui
        return {
            "sql_query": "SKIP",
            "error": None,
            "pending_select": {"table": table},
            "workflow_payload": payload,
            "messages": [AIMessage(content=message)],
        }

    def _maybe_disambiguate_filters(
        self,
        table: str,
        explicit_filters: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any] | None]:
        filters = dict(explicit_filters or {})

        facility_keys = [k for k in ("facility_name", "facility", "site", "location") if str(filters.get(k, "")).strip()]
        if facility_keys:
            facility_key = facility_keys[0]
            facility_value = str(filters.get(facility_key, "")).strip()
            candidates = self._lookup_facility_candidates(facility_value, metadata)
            used_fuzzy = bool(getattr(self, "_last_facility_lookup_used_fuzzy", False))
            if candidates:
                exact = [x for x in candidates if x.lower() == facility_value.lower()]
                if exact:
                    filters["facility_name"] = exact[0]
                elif len(candidates) == 1 and not used_fuzzy:
                    filters["facility_name"] = candidates[0]
                else:
                    options = self._compact_label_options([(name, f"facility_name={name}") for name in candidates])
                    return filters, self._build_disambiguation_prompt(table, filters, "facility_name", options)
            else:
                options = self._fallback_facility_options(metadata)
                if options:
                    return filters, self._build_disambiguation_prompt(table, filters, "facility_name", options)
            for alias in ("facility", "site", "location"):
                filters.pop(alias, None)

        user_keys = [k for k in ("assigned_to", "assignee", "user") if str(filters.get(k, "")).strip()]
        if user_keys and not str(filters.get("assigned_user_id", "")).strip():
            user_key = user_keys[0]
            user_value = str(filters.get(user_key, "")).strip()
            user_lower = user_value.lower()

            if user_lower in {"me", "my", "mine", "myself", "self", "current_user"}:
                resolved_name = str((metadata or {}).get("user_name") or "").strip()
                if resolved_name:
                    filters["assignee"] = resolved_name
                else:
                    actor_user_id = str((metadata or {}).get("user_id") or "").strip()
                    if actor_user_id:
                        filters["assigned_user_id"] = actor_user_id
                for alias in ("assigned_to", "user"):
                    filters.pop(alias, None)
                return filters, None

            if user_lower in {"", "task", "tasks", "status", "today", "yesterday", "all", "everyone"}:
                for alias in ("assigned_to", "user"):
                    filters.pop(alias, None)
                return filters, None

            candidates = self._lookup_user_candidates(user_value, metadata)
            used_fuzzy = bool(getattr(self, "_last_user_lookup_used_fuzzy", False))
            if candidates:
                exact = [c for c in candidates if str(c[0] or "").strip().lower() == user_value.lower()]
                chosen = exact[0] if exact else None
                if chosen is None and len(candidates) == 1 and not used_fuzzy:
                    chosen = candidates[0]

                if chosen is not None:
                    val_expr = str(chosen[1]).strip()
                    if "=" in val_expr:
                        key, value = val_expr.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key and value:
                            filters[key] = value
                else:
                    options = self._compact_label_options(candidates)
                    return filters, self._build_disambiguation_prompt(table, filters, "assignee", options)
            else:
                if len(user_value) >= 2:
                    options = self._fallback_user_options(metadata)
                    if options:
                        return filters, self._build_disambiguation_prompt(table, filters, "assignee", options)
            # Only drop aliases after we have a concrete assigned_user_id.
            if str(filters.get("assigned_user_id", "")).strip() or str(filters.get("assignee", "")).strip():
                for alias in ("assigned_to", "user"):
                    filters.pop(alias, None)

        return filters, None

    @staticmethod
    def _filter_options_excluding_prefilled(
        options: list[Dict[str, str]],
        prefilled_filters: Dict[str, Any] | None = None,
    ) -> list[Dict[str, str]]:
        prefilled = {str(k or "").strip().lower() for k in (prefilled_filters or {}).keys() if str(k or "").strip()}
        if not prefilled:
            return list(options or [])

        filtered: list[Dict[str, str]] = []
        for opt in options or []:
            value = str((opt or {}).get("value", "")).strip()
            if "=" in value:
                field = str(value.split("=", 1)[0]).strip().lower()
                if field and field in prefilled:
                    continue
            filtered.append(opt)
        return filtered

    def _filter_prompt_payload(
        self,
        table: str,
        suggested_fields: list[str],
        prefilled_filters: Dict[str, Any] | None = None,
        options_override: list[Dict[str, str]] | None = None,
    ) -> Dict:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        if not fields:
            fields = ["id", "name", "date_created"]
        
        # Generate dynamic options based on table schema
        dynamic_options = options_override or self._generate_dynamic_filter_options(table)
        
        # Generate example based on first option
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

    def _filter_prompt_message(
        self,
        table: str,
        prefilled_filters: Dict[str, Any] | None = None,
        focus_date: bool = False,
        options_override: list[Dict[str, str]] | None = None,
    ) -> str:
        """Generate filter prompt message with dynamic options."""
        dynamic_options = options_override or self._generate_dynamic_filter_options(table)
        
        # Extract just the values for display
        option_values = [opt["value"] for opt in dynamic_options]
        
        if focus_date:
            lines = [f"Choose a date to check `{table}` status."]
        else:
            lines = [f"Let me help you narrow down `{table}`."]
        lines.append("Pick an option number/value, or type filters directly. Use `back`/`cancel` anytime.")
        if prefilled_filters:
            if prefilled_filters.get("assigned_user_id"):
                lines.append("Defaulting to your assigned tasks. Type `assigned_user_id=<id>` to change.")


        
        # Add example based on table
        if "status" in [opt["value"].split("=")[0] for opt in dynamic_options if "=" in opt["value"]]:
            example = "status=Completed, scheduled_date=2025-07-18"
        elif "is_active" in [opt["value"].split("=")[0] for opt in dynamic_options if "=" in opt["value"]]:
            example = "is_active=1, name=example"
        else:
            example = "id=123, name=example"
        
        lines.append(f"Example: {example}")
        return "\n".join(lines)

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")
        actor_user_id = metadata.get("user_id") or metadata.get("userId")

        # If user already supplied SQL, pass it through untouched.
        # Validation and safety checks happen in sql_validate_node.
        if self._looks_like_sql_statement(query):
            return {"sql_query": query.strip()}

        # INTELLIGENT INTENT DETECTION
        # Use LLM to understand what the user really wants
        detected_intent = await self.intent_detector.detect_intent(query, metadata)
        logger.info(f"Detected intent: {detected_intent}")
        
        # Merge detected intent with existing intent (detected takes priority)
        intent = dict(state.get("intent") or {})
        if detected_intent.get("table"):
            intent["table"] = detected_intent["table"]
        if detected_intent.get("operation"):
            intent["operation"] = detected_intent["operation"]
        if detected_intent.get("filters"):
            # Merge filters from intent detection
            existing_filters = intent.get("filters", {})
            if isinstance(existing_filters, dict):
                for filter_obj in detected_intent["filters"]:
                    field = filter_obj.get("field")
                    value = filter_obj.get("value")
                    if (
                        field
                        and value
                        and not self._is_placeholder_filter_value(value)
                        and str(value).strip().lower() != str(field).strip().lower()
                    ):
                        existing_filters[field] = value
                intent["filters"] = existing_filters
        
        forced_table = self._extract_forced_table_from_query(query)
        prefilters = self._normalized_user_filters(intent.get("filters"), query)
        pending_table = str(metadata.get("pending_select_table", "") or "").strip()
        table_names = set(self.sql_builder.catalog.table_names() or [])
        pure_filter_query = self._is_pure_filter_query(query)

        operation = str(intent.get("operation", "select") or "select").lower()

        # Strict mode for filter-only input: use pending context table or ask for explicit table.
        if pure_filter_query and not forced_table and not self._query_mentions_explicit_table(query):
            if pending_table and pending_table in table_names:
                table = pending_table
            else:
                return {
                    "sql_query": "SKIP",
                    "error": None,
                    "messages": [
                        AIMessage(
                            content=(
                                "I need context for that filter input. "
                                "Please start with a table/entity like `show tasks` first, then apply filters."
                            )
                        )
                    ],
                }
        else:
            # Use forced table first (for pending-select followups), then detected/resolved.
            table = forced_table or intent.get("table") or self.sql_builder.resolve_table(query, intent)
        if (
            not forced_table
            and operation == "select"
            and self._looks_like_task_intent(query, prefilters)
            and not self._query_mentions_explicit_table(query)
        ):
            table = "task_transaction"
        if not table:
            return {
                "sql_query": "SKIP",
                "messages": [AIMessage(content="Please mention a table/entity like task, asset, user, or facility.")],
            }

        fields = {}
        if isinstance(intent.get("fields"), dict):
            fields.update(intent.get("fields"))
        kv_pairs = self.sql_builder.parse_kv_pairs(query)
        fields.update(kv_pairs)
        explicit_filters = prefilters
        explicit_filters, disambiguation_result = self._maybe_disambiguate_filters(table, explicit_filters, metadata)
        if disambiguation_result is not None:
            return disambiguation_result

        is_task_status = table == "task_transaction" and operation == "select"
        user_filter_keys = {"assigned_user_id", "assignee", "user_id", "user", "assigned_to"}
        
        # Only default to current user if NO user filter interpretation was found
        if (
            is_task_status
            and actor_user_id
            and not any(k in explicit_filters for k in user_filter_keys)
            and not self._mentions_explicit_nonself_user(query)
            and not self._requests_all_users(query)
            and self._requests_self_tasks(query)
        ):
            # Default to current user's tasks unless caller specified another user
            explicit_filters["assigned_user_id"] = actor_user_id

        # For task status views, assignee-only filters without an explicit date
        # become too broad; default to today unless user asked for another date.
        if (
            is_task_status
            and any(k in explicit_filters for k in user_filter_keys)
            and not str(explicit_filters.get("scheduled_date", "")).strip()
        ):
            lowered_query = str(query or "").lower()
            if not re.search(r"\b(yesterday|last week|this week|month|range|between)\b", lowered_query):
                explicit_filters["scheduled_date"] = "today"

        display_filters = self._sanitize_prefilled_filters(table, explicit_filters)

        # For natural-language task requests, show options menu.
        # Structured key=value follow-ups should continue to SQL execution.
        if is_task_status and not kv_pairs and not self._has_task_autorun_context(explicit_filters):
            candidate_filters = [
                "scheduled_date",
                "status",
                "facility_id",
                "location_level_id",
                "priority",
            ]
            task_options = [
                {"label": "Today (your tasks)", "value": "scheduled_date=today, assigned_to=current_user"},
                {"label": "Yesterday", "value": "scheduled_date=yesterday"},
                {"label": "Pick a date (YYYY-MM-DD)", "value": "scheduled_date="},
                {"label": "Different user / assignee", "value": "assignee="},
                {"label": "Facility or site", "value": "facility_name="},
                {"label": "Location level", "value": "location_level_id="},
                {"label": "Status", "value": "status="},
                {"label": "Priority", "value": "priority="},
            ]
            # If user/assignee already supplied in query, don't ask "Different user" again.
            if any(k in display_filters for k in user_filter_keys):
                task_options = [opt for opt in task_options if opt.get("value") != "assignee="]
            task_options = self._filter_options_excluding_prefilled(task_options, display_filters)
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(
                    table,
                    candidate_filters,
                    prefilled_filters=display_filters,
                    options_override=task_options,
                ),
                "messages": [AIMessage(content="")],
            }

        explicit_list_request = self._is_explicit_list_request(query, str(table))

        # Generic understanding flow for other SELECT queries:
        # keep inferred filters and ask only for remaining helpful filters.
        if operation == "select" and not is_task_status and not kv_pairs and not explicit_list_request:
            candidate_filters = [
                c
                for c in sorted(self.sql_builder.catalog.important_columns(table))
                if c not in {"id", "company_id", "created_by", "updated_by", "date_created", "date_updated"}
            ][:6]
            generic_options = self._generate_dynamic_filter_options(table)
            generic_options = self._filter_options_excluding_prefilled(generic_options, display_filters)
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(
                    table,
                    candidate_filters,
                    prefilled_filters=display_filters,
                    options_override=generic_options,
                ),
                "messages": [AIMessage(content="")],
            }

        if operation == "insert":
            if not self.sql_builder.catalog.create_enabled(table):
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=f"Create operation is not configured for `{table}`.")],
                }

            required = self.sql_builder.catalog.required_create_fields(table)
            if required:
                missing = [f for f in required if not str(fields.get(f, "")).strip()]
                if missing:
                    return {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=f"Missing required fields for insert: {', '.join(missing)}")],
                        "workflow_payload": self.sql_builder.mutation_form_payload(table, "insert", required),
                    }
            sql, err = self.sql_builder.build_insert(table, fields, company_id, actor_user_id=actor_user_id)
            if err:
                return {"sql_query": "SKIP", "messages": [AIMessage(content=err)]}
            return {"sql_query": sql}

        if operation == "update":
            sql, err = self.sql_builder.build_update(table, fields, company_id, actor_user_id=actor_user_id)
            if err:
                required_update_fields = ["id"]
                update_targets = [k for k in fields.keys() if str(k) not in {"id", "company_id"}]
                if update_targets:
                    required_update_fields.extend([str(k) for k in update_targets if str(k).strip()])
                elif re.search(r"\bstatus\b", query.lower()):
                    required_update_fields.append("status")
                else:
                    required_update_fields.append("field_value")
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=err + " Use e.g. id=123, status=Completed.")],
                    "workflow_payload": self.sql_builder.mutation_form_payload(table, "update", required_update_fields),
                }
            return {"sql_query": sql}

        if not explicit_filters and not explicit_list_request:
            candidate_filters = [
                c
                for c in sorted(self.sql_builder.catalog.important_columns(table))
                if c not in {"id", "company_id", "created_by", "updated_by", "date_created", "date_updated"}
            ][:6]
            filter_hint = ", ".join(candidate_filters) if candidate_filters else "status, scheduled_date, priority"
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(table, candidate_filters),
                "messages": [
                    AIMessage(
                        content=""
                    )
                ],
            }

        if explicit_list_request and not explicit_filters:
            sql = await self.sql_builder.build_select(query, table, company_id)
            select_err = ""
        else:
            sql, select_err = self.sql_builder.build_select_from_filters(table, explicit_filters, company_id)
        if select_err:
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(table, sorted(self.sql_builder.catalog.important_columns(table))),
                "messages": [AIMessage(content="")],
            }
        
        # Allow unfiltered queries but add LIMIT to prevent large result sets
        if self._is_unfiltered_select(sql):
            # Add LIMIT 100 to unfiltered queries
            if "LIMIT" not in sql.upper():
                sql = sql.rstrip(";") + " LIMIT 100;"

        where_cols = self._select_where_columns(sql)
        table_cols = self.sql_builder.catalog.important_columns(table)
        requires_company_scope = bool(company_id) and "company_id" in table_cols
        if requires_company_scope and "company_id" not in where_cols and not explicit_list_request:
            candidate_filters = [
                c
                for c in sorted(table_cols)
                if c not in {"id", "company_id", "created_by", "updated_by", "date_created", "date_updated"}
            ][:5]
            filter_hint = ", ".join(candidate_filters) if candidate_filters else "status, date, priority"
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(table, candidate_filters),
                "messages": [
                    AIMessage(
                        content=""
                    )
                ],
            }

        non_tenant_filters = {c for c in where_cols if c != "company_id"}
        if requires_company_scope and not non_tenant_filters and not explicit_list_request:
            candidate_filters = [
                c
                for c in sorted(table_cols)
                if c not in {"id", "company_id", "created_by", "updated_by", "date_created", "date_updated"}
            ][:5]
            filter_hint = ", ".join(candidate_filters) if candidate_filters else "status, date, priority"
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(table, candidate_filters),
                "messages": [
                    AIMessage(
                        content=""
                    )
                ],
            }
        return {"sql_query": sql}
