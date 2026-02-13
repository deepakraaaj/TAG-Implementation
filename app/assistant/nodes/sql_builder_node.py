from typing import Dict

from langchain_core.messages import AIMessage

from app.assistant.services.sql_builder_service import SQLBuilderService


class SQLBuilderNode:
    def __init__(self):
        self.builder = SQLBuilderService()

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")

        intent = dict(state.get("intent") or {})
        operation = str(intent.get("operation", "select") or "select").lower()
        table = self.builder.resolve_table(query, intent)
        if not table:
            return {
                "sql_query": "SKIP",
                "messages": [AIMessage(content="Please mention a table/entity like task, asset, user, or facility.")],
            }

        fields = {}
        if isinstance(intent.get("fields"), dict):
            fields.update(intent.get("fields"))
        fields.update(self.builder.parse_kv_pairs(query))

        if operation == "insert":
            required = self.builder.catalog.required_create_fields(table)
            if required:
                missing = [f for f in required if f not in fields]
                if missing:
                    return {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=f"Missing required fields for insert: {', '.join(missing)}")],
                        "workflow_payload": self.builder.mutation_form_payload(table, "insert", required),
                    }
            sql, err = self.builder.build_insert(table, fields, company_id)
            if err:
                return {"sql_query": "SKIP", "messages": [AIMessage(content=err)]}
            return {"sql_query": sql}

        if operation == "update":
            sql, err = self.builder.build_update(table, fields, company_id)
            if err:
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=err + " Use e.g. id=123, status=Completed")],
                    "workflow_payload": self.builder.mutation_form_payload(table, "update", ["id", "field=value"]),
                }
            return {"sql_query": sql}

        sql = await self.builder.build_select(query, table, company_id)
        return {"sql_query": sql}
