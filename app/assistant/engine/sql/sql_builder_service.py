import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import get_settings
from app.services.core.llm_retry_service import ainvoke_with_retry
from app.services.core.token_usage_service import TokenUsageService

settings = get_settings()


class SQLBuilderService:
    def __init__(
        self,
        llm: Any,
        manifest_catalog: Any,
        domain_provider: Callable[[], Any],
        toon_service: Any,
    ):
        self.llm = llm
        self.catalog = manifest_catalog
        self.domain = domain_provider()
        self.toon = toon_service

    def _table_meta(self, table: str) -> Dict[str, Any]:
        if hasattr(self.catalog, "table_meta"):
            meta = self.catalog.table_meta(table)
            return dict(meta) if isinstance(meta, dict) else {}
        return {}

    def _tenant_scope(self, table: str) -> Dict[str, str]:
        allowed = self.catalog.important_columns(table) or set()
        meta = self._table_meta(table)
        payload = meta.get("tenant_scope") if isinstance(meta.get("tenant_scope"), dict) else {}

        configured_column = self._safe_ident(str(payload.get("column", "")).strip())
        if configured_column and configured_column not in allowed:
            configured_column = ""

        inferred_column = ""
        if not configured_column:
            for candidate in ("company_id", "tenant_id", "organization_id", "org_id", "account_id", "customer_id"):
                if candidate in allowed:
                    inferred_column = candidate
                    break

        column = configured_column or inferred_column
        template_var = self._safe_ident(str(payload.get("template_var", "")).strip()) or column
        metadata_key = self._safe_ident(str(payload.get("metadata_key", "")).strip()) or column
        return {
            "column": column,
            "template_var": template_var,
            "metadata_key": metadata_key,
        }

    def _tenant_template_context(self, table: str, tenant_value: Any) -> Dict[str, str]:
        scope = self._tenant_scope(table)
        value = self._safe_value(tenant_value)
        context: Dict[str, str] = {}
        template_var = str(scope.get("template_var", "")).strip()
        column = str(scope.get("column", "")).strip()
        if template_var:
            context[template_var] = value
        if column and column not in context:
            context[column] = value
        # Backward compatibility for existing templates.
        context.setdefault("company_id", value)
        return context

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
        if value is None:
            return True
        text = str(value).strip().lower()
        return text in {"", "null", "none", "undefined", "n/a", "na"}

    @staticmethod
    def _is_safe_sql_path(expr: str) -> bool:
        candidate = str(expr or "").strip()
        if not candidate:
            return False
        # Support aliases like "f.name" or "status".
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", candidate))

    def _template_filter_aliases(self, table: str) -> Dict[str, str]:
        meta = self._table_meta(table)
        payload = meta.get("template_filter_aliases") or {}
        aliases: Dict[str, str] = {}
        if not isinstance(payload, dict):
            return aliases
        for raw_key, raw_expr in payload.items():
            key = self._safe_ident(str(raw_key))
            expr = str(raw_expr or "").strip()
            if not key or not self._is_safe_sql_path(expr):
                continue
            aliases[key] = expr
        return aliases

    def _default_select_columns(self, table: str, allowed: set[str]) -> List[str]:
        preferred: List[str] = []
        tenant_column = str(self._tenant_scope(table).get("column", "")).strip()
        meta = self._table_meta(table)
        configured = [str(item).strip() for item in (meta.get("default_select_columns") or []) if str(item).strip()]
        for col in configured:
            if col in allowed and col not in preferred:
                preferred.append(col)
        if preferred:
            return preferred[:8]

        important = meta.get("important_columns") if isinstance(meta.get("important_columns"), dict) else {}
        for col in important.keys():
            ident = str(col).strip()
            if not ident:
                continue
            if ident not in allowed:
                continue
            if ident in {"created_by", "updated_by", "date_created", "date_updated"}:
                continue
            if tenant_column and ident == tenant_column:
                continue
            preferred.append(ident)
            if len(preferred) >= 8:
                break

        if preferred:
            return preferred
        return sorted(list(allowed))[:8]

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
        tenant_column = str(self._tenant_scope(table).get("column", "")).strip()
        normalized = {}
        for k, v in fields.items():
            ident = self._safe_ident(k)
            if not ident:
                continue
            if allowed and ident not in allowed:
                continue
            normalized[ident] = self._normalize_enum_value(ident, v)

        if tenant_column and tenant_column in allowed and company_id and tenant_column not in normalized:
            normalized[tenant_column] = company_id
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
        tenant_column = str(self._tenant_scope(table).get("column", "")).strip()
        record_id = fields.get("id")
        if not record_id:
            return "", "Update requires id=<record_id>."

        updates = {}
        for k, v in fields.items():
            ident = self._safe_ident(k)
            if not ident:
                continue
            if ident == "id":
                continue
            if tenant_column and ident == tenant_column:
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
        if tenant_column and tenant_column in allowed and company_id:
            where += f" AND {tenant_column}={self._safe_value(company_id)}"
        return f"UPDATE {table} SET {set_clause} WHERE {where};", ""

    def build_select_from_filters(self, table: str, filters: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        tenant_column = str(self._tenant_scope(table).get("column", "")).strip()
        # Try to use a pre-defined template first for better joins/labels
        template = self.catalog.get_query_template(table, "list")
        
        if template:
            # Inject company_id or user_id context into template variables
            # We use safe substitution or simple replace since templates are trusted
            context = self._tenant_template_context(table, company_id)
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
            special_template_filters = self._template_filter_aliases(table)
            
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

        selected_cols: List[str] = self._default_select_columns(table, allowed)

        where_parts: List[str] = []

        if tenant_column and tenant_column in allowed and company_id:
            where_parts.append(f"{tenant_column}={self._safe_value(company_id)}")

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

    def build_count_from_filters(self, table: str, filters: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        template = self.catalog.get_query_template(table, "count")
        if template:
            sql = str(template)
            for key, value in self._tenant_template_context(table, company_id).items():
                sql = sql.replace(f"{{{key}}}", str(value))
            return sql, ""

        select_sql, select_err = self.build_select_from_filters(table, filters, company_id)
        if select_err:
            return "", select_err

        normalized = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*;?\s*$", "", select_sql, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+ORDER\s+BY\s+.+$", "", normalized, flags=re.IGNORECASE)
        return f"SELECT COUNT(*) AS total_count FROM ({normalized}) count_rows;", ""

    @staticmethod
    def _token_minimization_enabled(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        raw = meta.get("token_minimization")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _build_select_prompt(query: str, context: str) -> str:
        return f"""
Return only JSON: {{"sql":"..."}}
Generate one SELECT query only.
Must include LIMIT 100.
Context:
{context}
User query: {query}
"""

    async def build_select_with_usage(
        self,
        query: str,
        table: str,
        company_id: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, int]]:
        template = self.catalog.get_query_template(table, "list")
        if template:
            sql = str(template)
            for k, v in self._tenant_template_context(table, company_id).items():
                sql = sql.replace(f"{{{k}}}", str(v))
            return sql, TokenUsageService.skipped_call()

        cols = list(self.catalog.important_columns(table))[:12] or ["*"]
        tenant_column = str(self._tenant_scope(table).get("column", "")).strip()
        where_hint = ""
        if company_id and tenant_column and tenant_column in self.catalog.important_columns(table):
            where_hint = f"WHERE {tenant_column} = {self._safe_value(company_id)}"

        context_plain = (
            f"Use table: {table}\n"
            f"Columns: {', '.join(cols)}\n"
            f"Respect this if applicable: {where_hint or 'no tenant clause'}"
        )
        context_toon = self.toon.encode(
            {
                "table": table,
                "columns": cols,
                "tenant_clause_hint": where_hint or "no tenant clause",
            }
        )
        prompt_without_toon = self._build_select_prompt(query, context_plain)
        prompt_with_toon = self._build_select_prompt(query, context_toon)
        use_toon = self._token_minimization_enabled(metadata)
        prompt = prompt_with_toon if use_toon else prompt_without_toon
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_select",
            )
            usage = TokenUsageService.from_response(
                response,
                prompt_with_toon=prompt,
                prompt_without_toon=prompt_without_toon,
                toon_applied=use_toon,
            )
            raw = str(response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw[start : end + 1])
                sql = str(parsed.get("sql", "")).strip()
                if sql:
                    return sql, usage
        except Exception:
            pass

        tenant = f" WHERE {tenant_column} = {self._safe_value(company_id)}" if where_hint else ""
        return f"SELECT * FROM {table}{tenant} LIMIT 100;", TokenUsageService.empty()

    async def build_select(self, query: str, table: str, company_id: Any) -> str:
        sql, _usage = await self.build_select_with_usage(query, table, company_id, metadata=None)
        return sql
