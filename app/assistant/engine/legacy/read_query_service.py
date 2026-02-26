import re
from typing import Any, Dict, List, Tuple


class ReadQueryService:
    """Builds read-oriented SQL (select/count) independent of mutation SQL builder concerns."""

    def __init__(self, catalog: Any, domain: Any):
        self.catalog = catalog
        self.domain = domain

    def _query_policy(self) -> Dict[str, Any]:
        domain_cfg = getattr(self.domain, "config", {})
        if not isinstance(domain_cfg, dict):
            return {}
        policy = domain_cfg.get("query_policy")
        return policy if isinstance(policy, dict) else {}

    def _policy_list(self, key: str, default: List[str]) -> List[str]:
        value = self._query_policy().get(key)
        if isinstance(value, list):
            out = [str(x).strip() for x in value if str(x).strip()]
            if out:
                return out
        return list(default)

    def _policy_str(self, key: str, default: str) -> str:
        value = self._query_policy().get(key)
        text_value = str(value or "").strip()
        return text_value or default

    def _tenant_column(self) -> str:
        return self._safe_ident(self._policy_str("tenant_column", "company_id")) or "company_id"

    def _system_columns(self) -> set[str]:
        default_cols = ["id", self._tenant_column(), "created_by", "updated_by", "date_created", "date_updated"]
        return set(self._policy_list("system_columns", default_cols))

    @staticmethod
    def _safe_ident(name: str) -> str:
        return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or "") else ""

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

    def _normalize_enum_value(self, column: str, value: Any) -> Any:
        col = str(column or "").strip().lower()
        if value is None:
            return value
        text_value = str(value).strip()
        if text_value.isdigit():
            return int(text_value)
        mapper = getattr(self.domain, "get_enum_mapping", None)
        if callable(mapper):
            return mapper(col, value)
        return value

    def _template_special_filters(self, table: str) -> Dict[str, str]:
        domain_cfg = getattr(self.domain, "config", {})
        cfg = domain_cfg if isinstance(domain_cfg, dict) else {}
        mappings = cfg.get("template_filter_mappings")
        if isinstance(mappings, dict):
            table_map = mappings.get(table)
            if isinstance(table_map, dict):
                normalized: Dict[str, str] = {}
                for k, v in table_map.items():
                    key = self._safe_ident(str(k))
                    value = str(v or "").strip()
                    if key and value:
                        normalized[key] = value
                if normalized:
                    return normalized
        return {}

    def _build_template_filter_clauses(self, table: str, filters: Dict[str, Any]) -> List[str]:
        where_parts: List[str] = []
        allowed = self.catalog.important_columns(table)
        special_template_filters = self._template_special_filters(table)
        for k, raw_v in (filters or {}).items():
            ident = self._safe_ident(str(k))
            if not ident:
                continue
            is_special = ident in special_template_filters
            if not is_special and allowed and ident not in allowed:
                continue
            value = self._normalize_enum_value(ident, raw_v)
            text_value = str(value or "").strip().lower()
            if self._is_placeholder_filter_value(value):
                continue
            if text_value == ident.lower():
                continue
            if is_special:
                where_parts.append(f"{special_template_filters[ident]}={self._safe_value(value)}")
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
        return where_parts

    @staticmethod
    def _inject_template_filters(base_sql: str, where_parts: List[str]) -> str:
        if not where_parts:
            return base_sql
        upper_sql = base_sql.upper()
        insert_pos = len(base_sql)
        order_idx = upper_sql.rfind(" ORDER BY ")
        limit_idx = upper_sql.rfind(" LIMIT ")
        if order_idx != -1:
            insert_pos = order_idx
        elif limit_idx != -1:
            insert_pos = limit_idx
        additional_where = " AND " + " AND ".join(where_parts)
        if " WHERE " not in upper_sql[:insert_pos]:
            additional_where = " WHERE " + " AND ".join(where_parts)
        return base_sql[:insert_pos] + additional_where + base_sql[insert_pos:]

    def build_select_from_filters(self, table: str, filters: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        tenant_col = self._tenant_column()
        template_getter = getattr(self.catalog, "get_query_template", None)
        template = template_getter(table, "list") if callable(template_getter) else ""
        if template:
            tenant_value = self._safe_value(company_id)
            context = {"company_id": tenant_value, tenant_col: tenant_value}
            if "user_id" in template and "user_id" not in context:
                context["user_id"] = "NULL"
            base_sql = template
            for k, v in context.items():
                base_sql = base_sql.replace(f"{{{k}}}", str(v))
            where_parts = self._build_template_filter_clauses(table, filters)
            return self._inject_template_filters(base_sql, where_parts), ""

        allowed = self.catalog.important_columns(table)
        if not allowed:
            return "", "Unknown table metadata for select."

        selected_cols: List[str] = []
        default_cols = self._query_policy().get("default_select_columns")
        if not isinstance(default_cols, list):
            default_cols = []
        for key in [str(x).strip() for x in default_cols if str(x).strip()]:
            if key in allowed:
                selected_cols.append(key)
        if not selected_cols:
            selected_cols = [c for c in sorted(list(allowed)) if c not in self._system_columns()][:8]
        if not selected_cols:
            selected_cols = sorted(list(allowed))[:8]

        where_parts: List[str] = []
        if tenant_col in allowed and company_id:
            where_parts.append(f"{tenant_col}={self._safe_value(company_id)}")

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
        return f"SELECT {cols} FROM {table} WHERE {where} LIMIT 100;", ""

    def build_count_from_filters(self, table: str, filters: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        tenant_col = self._tenant_column()
        template_getter = getattr(self.catalog, "get_query_template", None)
        template = template_getter(table, "count") if callable(template_getter) else ""
        if template:
            tenant_value = str(self._safe_value(company_id))
            base_sql = template.replace("{company_id}", tenant_value).replace(f"{{{tenant_col}}}", tenant_value)
            where_parts = self._build_template_filter_clauses(table, filters)
            return self._inject_template_filters(base_sql, where_parts), ""

        select_sql, select_err = self.build_select_from_filters(table, filters, company_id)
        if select_err:
            return "", select_err
        normalized = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*;?\s*$", "", select_sql, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+ORDER\s+BY\s+.+$", "", normalized, flags=re.IGNORECASE)
        return f"SELECT COUNT(*) AS total_count FROM ({normalized}) count_rows;", ""
