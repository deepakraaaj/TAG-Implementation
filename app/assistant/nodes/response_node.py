from typing import Dict

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp
import re


class ResponseNode:
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
            if ResponseNode._is_hidden_filter_key(left):
                continue
            right = node.expression.sql(dialect="mysql")
            filters.append(f"{left}={right}")
        for node in where.find_all(exp.Like):
            left = node.this.sql(dialect="mysql")
            if ResponseNode._is_hidden_filter_key(left):
                continue
            right = node.expression.sql(dialect="mysql")
            filters.append(f"{left} LIKE {right}")
        return filters[:6]

    @staticmethod
    def _friendly_no_records_message(sql: str, metadata: Dict | None = None) -> str:
        text_sql = str(sql or "")
        lowered = text_sql.lower()
        meta = metadata or {}

        parts = []

        if "date(scheduled_date) = curdate()" in lowered:
            parts.append("today")

        assignee_match = re.search(
            r"like\s+lower\('%([^']+)%'\)",
            text_sql,
            flags=re.IGNORECASE,
        )
        assignee_name = ""
        if assignee_match:
            assignee_name = str(assignee_match.group(1) or "").strip()
            if assignee_name:
                parts.append(f"assignee '{assignee_name}'")

        if not assignee_name:
            id_match = re.search(r"\bassigned_user_id\s*=\s*(\d+)", text_sql, flags=re.IGNORECASE)
            if id_match:
                sql_uid = str(id_match.group(1) or "").strip()
                meta_uid = str(meta.get("user_id") or meta.get("userId") or "").strip()
                if meta_uid and sql_uid == meta_uid:
                    assignee_name = str(meta.get("user_name") or "").strip()
                    if assignee_name:
                        parts.append(f"assignee '{assignee_name}'")

        facility_match = re.search(r"f\.name\s*=\s*'([^']+)'", text_sql, flags=re.IGNORECASE)
        if facility_match:
            facility = str(facility_match.group(1) or "").strip()
            if facility:
                parts.append(f"facility '{facility}'")

        status_match = re.search(r"\bstatus\s*=\s*'([^']+)'", text_sql, flags=re.IGNORECASE)
        if status_match:
            status = str(status_match.group(1) or "").strip()
            if status:
                parts.append(f"status '{status}'")

        if "today" in parts and assignee_name:
            first = assignee_name.split()[0].strip()
            display_name = first if first else assignee_name
            return f"{display_name}, you don't have tasks today."
        if parts:
            return "No records found for " + ", ".join(parts) + "."
        return "No records found for the selected filters."

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
                msg = self._friendly_no_records_message(raw_sql, metadata=state.get("metadata") or {})
            else:
                msg = f"Found {count} record(s)."

        return {"messages": [AIMessage(content=msg)]}
