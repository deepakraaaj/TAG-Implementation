from typing import Dict

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp

from app.domains.registry import DomainRegistry


class ResponseNode:
    @staticmethod
    def _friendly_error_message(error_text: str, raw_sql: str = "") -> str:
        err = str(error_text or "").strip()
        sql_text = str(raw_sql or "").strip()
        err_lower = err.lower()
        sql_lower = sql_text.lower()

        if ("1064" in err or "syntax" in err_lower) and sql_lower.startswith("update"):
            return "This operation is still under development. I will support it soon."
        if ("1064" in err or "syntax" in err_lower) and sql_text:
            return "This operation is still under development. I will support it soon."
        return "I could not complete that request right now. Please try again with more specific details."

    @staticmethod
    def _is_hidden_filter_key(key: str) -> bool:
        k = str(key or "").strip().lower()
        if not k:
            return False
        if k == "id" or k.endswith(".id"):
            return True
        if k.endswith("_id") or ".company_id" in k or k == "company_id":
            return True
        return False

    @staticmethod
    def _literal_text(node: exp.Expression | None) -> str:
        if isinstance(node, exp.Literal):
            return str(node.this or "").strip()
        if isinstance(node, exp.Paren):
            return ResponseNode._literal_text(node.this if isinstance(node.this, exp.Expression) else None)
        if isinstance(node, exp.Expression):
            for value in node.args.values():
                if isinstance(value, exp.Expression):
                    literal = ResponseNode._literal_text(value)
                    if literal:
                        return literal
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, exp.Expression):
                            literal = ResponseNode._literal_text(item)
                            if literal:
                                return literal
        return ""

    @staticmethod
    def _is_current_date_expr(node: exp.Expression | None) -> bool:
        if node is None:
            return False
        sql_text = node.sql(dialect="mysql").strip().upper()
        return sql_text in {"CURDATE()", "CURRENT_DATE()", "CURRENT_DATE"}

    @staticmethod
    def _extract_where_filters(sql: str) -> list[str]:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return []
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return []
        if not isinstance(parsed, exp.Select):
            return []
        where = parsed.args.get("where")
        if where is None:
            return []

        filters: list[str] = []
        for node in where.find_all(exp.EQ):
            left_expr = node.this
            right_expr = node.expression
            left_column = left_expr if isinstance(left_expr, exp.Column) else None
            right_column = right_expr if isinstance(right_expr, exp.Column) else None
            if left_column is None and right_column is not None:
                left_expr, right_expr = right_expr, left_expr
                left_column = left_expr if isinstance(left_expr, exp.Column) else None

            if left_column is None:
                continue

            left_name = str(left_column.name or "").strip()
            if ResponseNode._is_hidden_filter_key(left_name):
                continue

            if ResponseNode._is_current_date_expr(right_expr):
                continue

            right_literal = ResponseNode._literal_text(right_expr)
            if right_literal:
                filters.append(f"{left_name}='{right_literal}'")
                continue

            right_rendered = right_expr.sql(dialect="mysql")
            if "CURDATE()" in right_rendered.upper():
                continue
            filters.append(f"{left_name}={right_rendered}")

        for node in where.find_all(exp.Like):
            left_expr = node.this
            if isinstance(left_expr, exp.Column):
                left_name = str(left_expr.name or "").strip()
                if ResponseNode._is_hidden_filter_key(left_name):
                    continue
            raw_pattern = ResponseNode._literal_text(node.expression)
            if not raw_pattern:
                continue
            cleaned = raw_pattern.strip("%").strip()
            if not cleaned:
                continue
            filters.append(f"'{cleaned}'")

        return filters[:6]

    @staticmethod
    def _sql_operation(sql: str) -> str:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return ""
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return ""
        if isinstance(parsed, exp.Insert):
            return "insert"
        if isinstance(parsed, exp.Update):
            return "update"
        if isinstance(parsed, exp.Select):
            return "select"
        return ""

    @staticmethod
    def _user_id_filter_keys() -> set[str]:
        keys: set[str] = set()
        try:
            domain = DomainRegistry.get_current_domain()
            lookup = domain.get_user_lookup_config()
            explicit_key = str(lookup.get("id_filter_key", "")).strip().lower()
            if explicit_key:
                keys.add(explicit_key)
            behavior = domain.get_entity_behavior_config()
            for item in behavior.get("user_filter_keys") or []:
                key = str(item or "").strip().lower()
                if key.endswith("_id"):
                    keys.add(key)
        except Exception:
            pass
        if not keys:
            keys.update({"assigned_user_id", "user_id"})
        return keys

    @staticmethod
    def _is_self_today_query(sql: str, metadata: Dict | None = None) -> bool:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return False
        meta = metadata or {}
        metadata_user_id = str(meta.get("user_id") or meta.get("userId") or "").strip()
        if not metadata_user_id:
            return False
        try:
            parsed = sqlglot.parse_one(text_sql)
        except Exception:
            return False
        if not isinstance(parsed, exp.Select):
            return False
        where = parsed.args.get("where")
        if where is None:
            return False

        has_current_date = "CURDATE()" in where.sql(dialect="mysql").upper()
        if not has_current_date:
            return False

        user_id_keys = ResponseNode._user_id_filter_keys()
        for node in where.find_all(exp.EQ):
            left_expr = node.this
            right_expr = node.expression
            left_column = left_expr if isinstance(left_expr, exp.Column) else None
            right_column = right_expr if isinstance(right_expr, exp.Column) else None
            if left_column is None and right_column is not None:
                left_expr, right_expr = right_expr, left_expr
                left_column = left_expr if isinstance(left_expr, exp.Column) else None
            if left_column is None:
                continue
            key = str(left_column.name or "").strip().lower()
            if key not in user_id_keys:
                continue
            literal = ResponseNode._literal_text(right_expr)
            if literal and literal == metadata_user_id:
                return True
        return False

    @staticmethod
    def _company_name(metadata: Dict | None = None) -> str:
        meta = metadata or {}
        company_obj = meta.get("company")
        company_obj_name = ""
        if isinstance(company_obj, dict):
            company_obj_name = str(company_obj.get("name") or "").strip()
        for candidate in (
            meta.get("company_name"),
            meta.get("companyName"),
            company_obj_name,
        ):
            cleaned = str(candidate or "").strip()
            if cleaned:
                return cleaned
        return ""

    @staticmethod
    def _self_display_name(metadata: Dict | None = None) -> str:
        meta = metadata or {}
        assignee_name = str(meta.get("user_name") or "").strip()
        if not assignee_name:
            return ""
        lowered = assignee_name.casefold()
        if lowered in {"user", "unknown", "na", "n/a", "null", "none"}:
            return ""
        company_name = ResponseNode._company_name(meta)
        if company_name and lowered == company_name.casefold():
            return ""
        first = assignee_name.split()[0].strip()
        return first if first else assignee_name

    @staticmethod
    def _friendly_no_records_message(sql: str, metadata: Dict | None = None) -> str:
        meta = metadata or {}
        domain = DomainRegistry.get_current_domain()

        domain_message = domain.format_no_records_message(str(sql or ""), metadata=meta)
        if domain_message:
            return domain_message

        is_self_today = ResponseNode._is_self_today_query(sql, metadata=meta)
        if is_self_today:
            display_name = ResponseNode._self_display_name(meta)
            template = domain.get_response_message("self_no_records_today", "")
            if display_name:
                if template:
                    return template.replace("{name}", display_name)
                return f"{display_name}, you have no records for today."
            if template and "{name}" not in template:
                return template
            return "You don't have tasks today."

        filters = ResponseNode._extract_where_filters(sql)
        if filters:
            return "No records found for " + ", ".join(filters) + "."

        return domain.get_response_message("no_records_default", "No records found for the selected filters.")

    async def run(self, state: Dict) -> Dict:
        if state.get("error"):
            raw_sql = str(state.get("sql_query") or "")
            friendly = self._friendly_error_message(str(state["error"]), raw_sql=raw_sql)
            return {"messages": [AIMessage(content=friendly)]}

        raw_sql = (state.get("sql_query") or "").strip()
        operation = self._sql_operation(raw_sql)
        count = int(state.get("row_count") or 0)

        if operation == "insert":
            msg = f"Insert successful. Rows affected: {count}."
        elif operation == "update":
            msg = f"Update successful. Rows affected: {count}."
        else:
            if count == 0:
                msg = self._friendly_no_records_message(raw_sql, metadata=state.get("metadata") or {})
            else:
                msg = f"Found {count} record(s)."

        return {"messages": [AIMessage(content=msg)]}
