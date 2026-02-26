from typing import Set

import sqlglot
from sqlglot import exp


class QueryGuardService:
    @staticmethod
    def is_unfiltered_select(sql: str) -> bool:
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
    def select_where_columns(sql: str) -> Set[str]:
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
    def ensure_limit(sql: str, limit: int = 100) -> str:
        text_sql = str(sql or "").strip()
        if not text_sql:
            return text_sql
        if QueryGuardService.is_unfiltered_select(text_sql) and "LIMIT" not in text_sql.upper():
            return text_sql.rstrip(";") + f" LIMIT {int(limit)};"
        return text_sql
