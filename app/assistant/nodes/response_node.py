from typing import Dict

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp


class ResponseNode:
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
            left = node.this.sql(dialect="mysql")
            right = node.expression.sql(dialect="mysql")
            filters.append(f"{left}={right}")
        for node in where.find_all(exp.Like):
            left = node.this.sql(dialect="mysql")
            right = node.expression.sql(dialect="mysql")
            filters.append(f"{left} LIKE {right}")
        return filters[:6]

    async def run(self, state: Dict) -> Dict:
        if state.get("error"):
            return {"messages": [AIMessage(content=f"Request failed safely: {state['error']}")]}

        sql = (state.get("sql_query") or "").strip().upper()
        count = int(state.get("row_count") or 0)
        preview = state.get("rows_preview") or []
        raw_sql = (state.get("sql_query") or "").strip()

        if sql.startswith("INSERT"):
            msg = f"Insert successful. Rows affected: {count}."
        elif sql.startswith("UPDATE"):
            msg = f"Update successful. Rows affected: {count}."
        else:
            if count == 0:
                filters = self._extract_where_filters(raw_sql)
                if filters:
                    msg = (
                        "No records found for the exact filters: "
                        + ", ".join(filters)
                        + ". Please verify each parameter value (especially IDs and exact names), "
                        + "or use a broader match with LIKE/date range."
                    )
                else:
                    msg = "No records found."
            else:
                msg = f"Found {count} record(s). Preview: {preview[:3]}"

        return {"messages": [AIMessage(content=msg)]}
