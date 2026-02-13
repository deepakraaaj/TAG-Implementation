from typing import Dict

from langchain_core.messages import AIMessage


class ResponseNode:
    async def run(self, state: Dict) -> Dict:
        if state.get("error"):
            return {"messages": [AIMessage(content=f"Request failed safely: {state['error']}")]}

        sql = (state.get("sql_query") or "").strip().upper()
        count = int(state.get("row_count") or 0)
        preview = state.get("rows_preview") or []

        if sql.startswith("INSERT"):
            msg = f"Insert successful. Rows affected: {count}."
        elif sql.startswith("UPDATE"):
            msg = f"Update successful. Rows affected: {count}."
        else:
            msg = "No records found." if count == 0 else f"Found {count} record(s). Preview: {preview[:3]}"

        return {"messages": [AIMessage(content=msg)]}
