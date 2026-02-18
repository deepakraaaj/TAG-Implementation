import json
import os
import re
from typing import Any, Dict, Tuple, List

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

from app.assistant.services.manifest_catalog import ManifestCatalog
from app.domains.registry import DomainRegistry

settings = get_settings()


class SQLBuilderService:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        self.catalog = ManifestCatalog()
        self.domain = DomainRegistry.get_current_domain()

    @staticmethod
    def _safe_ident(name: str) -> str:
        return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or "") else ""

    @staticmethod
    def _enum_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())

    def _normalize_enum_value(self, column: str, value: Any) -> Any:
        col = str(column or "").strip().lower()
        if value is None:
            return value
        text_value = str(value).strip()
        if text_value.isdigit():
            return int(text_value)
        # Use domain registry for enum mapping
        return self.domain.get_enum_mapping(col, value)

    @staticmethod
    def _safe_value(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).strip().strip("'\"").replace("'", "''")
        return f"'{text}'"

    @staticmethod
    def _is_placeholder_filter_value(value: Any) -> bool:
        text = str(value or "").strip().lower()
        return text in {"", "null", "none", "undefined", "n/a", "na"}

    @staticmethod
    def parse_kv_pairs(text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not text:
            return out
        for pattern in [
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s+is\s+([^,;]+)",
        ]:
            for k, v in re.findall(pattern, text, flags=re.IGNORECASE):
                out[k.strip()] = v.strip().strip("'\"")
        return out

    def resolve_table(self, query: str, intent: Dict[str, Any]) -> str:
        table = str(intent.get("table", "") or "").strip()
        if table in self.catalog.table_names():
            return table
        return self.catalog.resolve_table_from_query(query)

    def build_insert(
        self,
        table: str,
        fields: Dict[str, Any],
        company_id: Any,
        actor_user_id: Any = None,
    ) -> Tuple[str, str]:
        allowed = self.catalog.important_columns(table)
        normalized = {}
        for k, v in fields.items():
            ident = self._safe_ident(k)
            if not ident:
                continue
            if allowed and ident not in allowed:
                continue
            normalized[ident] = self._normalize_enum_value(ident, v)

        if "company_id" in allowed and company_id and "company_id" not in normalized:
            normalized["company_id"] = company_id
        if "created_by" in allowed and actor_user_id and "created_by" not in normalized:
            normalized["created_by"] = actor_user_id
        if "updated_by" in allowed and actor_user_id and "updated_by" not in normalized:
            normalized["updated_by"] = actor_user_id

        if not normalized:
            return "", "No valid fields found for insert."

        cols = ", ".join(normalized.keys())
        vals = ", ".join(self._safe_value(v) for v in normalized.values())
        return f"INSERT INTO {table} ({cols}) VALUES ({vals});", ""

    def build_update(
        self,
        table: str,
        fields: Dict[str, Any],
        company_id: Any,
        actor_user_id: Any = None,
    ) -> Tuple[str, str]:
        allowed = self.catalog.important_columns(table)
        record_id = fields.get("id")
        if not record_id:
            return "", "Update requires id=<record_id>."

        updates = {}
        for k, v in fields.items():
            ident = self._safe_ident(k)
            if not ident or ident in {"id", "company_id"}:
                continue
            if allowed and ident not in allowed:
                continue
            updates[ident] = self._normalize_enum_value(ident, v)
        if "updated_by" in allowed and actor_user_id and "updated_by" not in updates:
            updates["updated_by"] = actor_user_id

        if not updates:
            return "", "Update requires at least one field to change."

        set_clause = ", ".join(f"{k}={self._safe_value(v)}" for k, v in updates.items())
        where = f"id={self._safe_value(record_id)}"
        if "company_id" in allowed and company_id:
            where += f" AND company_id={self._safe_value(company_id)}"
        return f"UPDATE {table} SET {set_clause} WHERE {where};", ""

    def build_select_from_filters(self, table: str, filters: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        # Try to use a pre-defined template first for better joins/labels
        template = self.catalog.get_query_template(table, "list")
        
        if template:
            # Inject company_id or user_id context into template variables
            # We use safe substitution or simple replace since templates are trusted
            context = {"company_id": self._safe_value(company_id)}
            # Some templates might need user_id, though not common in 'list'
            if "user_id" in template and "user_id" not in context:
                context["user_id"] = "NULL"  # Placeholder if missing
            
            # Basic formatting of the template base
            # Use simple replace to avoid key errors on partial format strings
            base_sql = template
            for k, v in context.items():
                base_sql = base_sql.replace(f"{{{k}}}", str(v))
            
            # Now append dynamic filters
            where_parts = []
            allowed = self.catalog.important_columns(table)
            special_template_filters = {}
            if table == "task_transaction":
                # Allow friendly facility/location name filters when template joins are present
                special_template_filters = {
                    "facility_name": "f.name",
                    "facility": "f.name",
                    "facility": "f.name",
                    "site": "f.name",
                    "location": "f.name",
                }
            
            for k, raw_v in (filters or {}).items():
                ident = self._safe_ident(str(k))
                if not ident:
                    continue
                # For templates, we might need to qualify columns, but usually the template handles joins
                # We assume simple filters apply to the main table or aliased columns
                # If identifier is not in allowed important columns, skip to be safe? 
                # Or trust the user filter if it matches a column in result?
                # Sticking to 'allowed' check is safer
                is_special = ident in special_template_filters
                if not is_special and allowed and ident not in allowed:
                    continue

                value = self._normalize_enum_value(ident, raw_v)
                text_value = str(value or "").strip().lower()
                if self._is_placeholder_filter_value(value):
                    continue
                if text_value == ident.lower():
                    continue
                
                # Check for table alias prefix if needed, but for now assumption is ambiguous cols are risky
                # We will just append AND ident = value. 
                # If template uses aliases (e.g. tt.status), we might need to know the alias.
                # However, SQL usually allows `status = ...` if unique, or we can try to alias if we know it.
                # Simplest robust way: use the raw ident. If it fails, it fails.
                
                clause = ""
                if is_special:
                    target_col = special_template_filters[ident]
                    clause = f"{target_col}={self._safe_value(value)}"
                elif ident.endswith("_date") and text_value == "today":
                    clause = f"DATE({ident}) = CURDATE()"
                elif ident.endswith("_date") and text_value == "yesterday":
                    clause = f"DATE({ident}) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
                elif text_value in {"is null", "null"}:
                    clause = f"{ident} IS NULL"
                elif text_value in {"is not null", "not null"}:
                    clause = f"{ident} IS NOT NULL"
                else:
                    clause = f"{ident}={self._safe_value(value)}"
                
                if clause:
                    where_parts.append(clause)
            
            if not where_parts:
                return base_sql, ""
                
            # Inject filters into the WHERE clause
            # Pattern: ... WHERE ... ORDER BY ... LIMIT ...
            # We want to insert " AND (filters) " before ORDER BY or LIMIT
            
            upper_sql = base_sql.upper()
            insert_pos = len(base_sql)
            
            # Try to find ORDER BY or LIMIT to insert before
            order_idx = upper_sql.rfind(" ORDER BY ")
            limit_idx = upper_sql.rfind(" LIMIT ")
            
            if order_idx != -1:
                insert_pos = order_idx
            elif limit_idx != -1:
                insert_pos = limit_idx
                
            additional_where = " AND " + " AND ".join(where_parts)
            
            # If template has no WHERE, we need to add WHERE. Use sophisticated check?
            # Most templates in manifest have WHERE company_id = ...
            # If not, we might need WHERE.
            if " WHERE " not in upper_sql[:insert_pos]:
                 additional_where = " WHERE " + " AND ".join(where_parts)
            
            final_sql = base_sql[:insert_pos] + additional_where + base_sql[insert_pos:]
            return final_sql, ""

        allowed = self.catalog.important_columns(table)
        if not allowed:
            return "", "Unknown table metadata for select."

        selected_cols: List[str] = []
        for key in ["id", "status", "task_id", "scheduled_date", "priority", "closed_time"]:
            if key in allowed:
                selected_cols.append(key)
        if not selected_cols:
            selected_cols = sorted(list(allowed))[:8]

        where_parts: List[str] = []

        if "company_id" in allowed and company_id:
            where_parts.append(f"company_id={self._safe_value(company_id)}")

        for k, raw_v in (filters or {}).items():
            ident = self._safe_ident(str(k))
            if not ident:
                continue
            if allowed and ident not in allowed:
                continue

            value = self._normalize_enum_value(ident, raw_v)
            text_value = str(value or "").strip().lower()
            if self._is_placeholder_filter_value(value):
                continue
            if text_value == ident.lower():
                continue
            if ident.endswith("_date") and text_value == "today":
                where_parts.append(f"DATE({ident}) = CURDATE()")
                continue
            if ident.endswith("_date") and text_value == "yesterday":
                where_parts.append(f"DATE({ident}) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)")
                continue
            if text_value in {"is null", "null"}:
                where_parts.append(f"{ident} IS NULL")
                continue
            if text_value in {"is not null", "not null"}:
                where_parts.append(f"{ident} IS NOT NULL")
                continue
            where_parts.append(f"{ident}={self._safe_value(value)}")

        if not where_parts:
            return "", "Select requires filters."

        cols = ", ".join(selected_cols)
        where = " AND ".join(where_parts)
        sql = f"SELECT {cols} FROM {table} WHERE {where} LIMIT 100;"
        return sql, ""

    async def build_select(self, query: str, table: str, company_id: Any) -> str:
        cols = list(self.catalog.important_columns(table))[:12] or ["*"]
        where_hint = ""
        if company_id and "company_id" in self.catalog.important_columns(table):
            where_hint = f"WHERE company_id = {self._safe_value(company_id)}"

        prompt = f"""
Return only JSON: {{"sql":"..."}}
Generate one SELECT query only.
Use table: {table}
Columns: {', '.join(cols)}
Must include LIMIT 100.
Respect this if applicable: {where_hint or 'no tenant clause'}
User query: {query}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_select",
            )
            raw = str(response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw[start : end + 1])
                sql = str(parsed.get("sql", "")).strip()
                if sql:
                    return sql
        except Exception:
            pass

        tenant = f" WHERE company_id = {self._safe_value(company_id)}" if where_hint else ""
        return f"SELECT * FROM {table}{tenant} LIMIT 100;"

    def mutation_form_payload(self, table: str, operation: str, required_fields):
        fields = [str(x) for x in required_fields]
        return {
            "workflow_id": "mutation_menu",
            "state": f"collect_{operation}_{table}",
            "completed": False,
            "collected_data": {
                "operation": operation,
                "table": table,
                "required_fields": fields,
            },
            "ui": {
                "type": "form",
                "state": f"collect_{operation}_{table}",
                "title": f"{operation.title()} {table}",
                "description": "Provide values as key=value pairs separated by commas.",
                "fields": [{"id": f, "label": f, "type": "text"} for f in fields],
            },
        }
