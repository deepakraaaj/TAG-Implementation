from typing import Dict
import re

from langchain_core.messages import AIMessage
import sqlglot
from sqlglot import exp

from app.assistant.services.sql_builder_service import SQLBuilderService


class SQLBuilderNode:
    def __init__(self):
        self.builder = SQLBuilderService()

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
                if key and value:
                    normalized[key] = value
        for k, v in SQLBuilderService.parse_kv_pairs(query).items():
            key = str(k or "").strip()
            value = str(v or "").strip()
            if key and value:
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
        return normalized

    @staticmethod
    def _filter_prompt_payload(table: str, suggested_fields: list[str]) -> Dict:
        fields = [str(x).strip() for x in suggested_fields if str(x).strip()]
        if not fields:
            fields = ["status", "scheduled_date", "priority"]
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
                "collected_fields": {},
            },
            "ui": {
                "type": "menu",
                "title": f"Add filters for {table}",
                "options": [
                    {"label": "Today", "value": "today"},
                    {"label": "Completed", "value": "status=Completed"},
                    {"label": "Pending", "value": "status=Pending"},
                    {"label": "In Progress", "value": "status=In Progress"},
                    {"label": "Overdue", "value": "status=Overdue"},
                    {"label": "Yesterday", "value": "yesterday"},
                ],
                "suggested_fields": fields[:6],
                "example": "status=Completed, scheduled_date=2025-07-18",
            },
        }

    @staticmethod
    def _filter_prompt_message(table: str) -> str:
        options = [
            "today",
            "status=Completed",
            "status=Pending",
            "status=In Progress",
            "status=Overdue",
            "yesterday",
        ]
        lines = [f"Choose filters for `{table}`"]
        for idx, opt in enumerate(options, start=1):
            lines.append(f"{idx}. {opt}")
        lines.append(
            "Type option number/value, or type your own filters. "
            "Use `back`/`cancel` to stop. Example: status=Completed, scheduled_date=2025-07-18."
        )
        return "\n".join(lines)

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = str(messages[-1].content) if messages else ""
        metadata = state.get("metadata", {})
        company_id = metadata.get("company_id")
        actor_user_id = metadata.get("user_id") or metadata.get("userId")

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
        explicit_filters = self._normalized_user_filters(intent.get("filters"), query)

        if operation == "insert":
            if not self.builder.catalog.create_enabled(table):
                return {
                    "sql_query": "SKIP",
                    "messages": [AIMessage(content=f"Create operation is not configured for `{table}`.")],
                }

            required = self.builder.catalog.required_create_fields(table)
            if required:
                missing = [f for f in required if not str(fields.get(f, "")).strip()]
                if missing:
                    return {
                        "sql_query": "SKIP",
                        "messages": [AIMessage(content=f"Missing required fields for insert: {', '.join(missing)}")],
                        "workflow_payload": self.builder.mutation_form_payload(table, "insert", required),
                    }
            sql, err = self.builder.build_insert(table, fields, company_id, actor_user_id=actor_user_id)
            if err:
                return {"sql_query": "SKIP", "messages": [AIMessage(content=err)]}
            return {"sql_query": sql}

        if operation == "update":
            sql, err = self.builder.build_update(table, fields, company_id, actor_user_id=actor_user_id)
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
                    "workflow_payload": self.builder.mutation_form_payload(table, "update", required_update_fields),
                }
            return {"sql_query": sql}

        if not explicit_filters:
            candidate_filters = [
                c
                for c in sorted(self.builder.catalog.important_columns(table))
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
                        content=self._filter_prompt_message(table)
                    )
                ],
            }

        sql, select_err = self.builder.build_select_from_filters(table, explicit_filters, company_id)
        if select_err:
            return {
                "sql_query": "SKIP",
                "error": None,
                "pending_select": {"table": table},
                "workflow_payload": self._filter_prompt_payload(table, sorted(self.builder.catalog.important_columns(table))),
                "messages": [AIMessage(content=self._filter_prompt_message(table))],
            }
        if self._is_unfiltered_select(sql):
            candidate_filters = [
                c
                for c in sorted(self.builder.catalog.important_columns(table))
                if c not in {"id", "created_by", "updated_by", "date_created", "date_updated"}
            ][:5]
            filter_hint = ", ".join(candidate_filters) if candidate_filters else "status, scheduled_date, priority"
            return {
                "sql_query": "SKIP",
                "error": "SELECT requires at least one WHERE filter.",
                "messages": [
                    AIMessage(
                        content=(
                            "I can't run broad SELECT queries without filters. "
                            f"Please provide at least one filter for `{table}`. "
                            f"Try fields like: {filter_hint}."
                        )
                    )
                ],
            }

        where_cols = self._select_where_columns(sql)
        table_cols = self.builder.catalog.important_columns(table)
        requires_company_scope = bool(company_id) and "company_id" in table_cols
        if requires_company_scope and "company_id" not in where_cols:
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
                        content=self._filter_prompt_message(table)
                    )
                ],
            }

        non_tenant_filters = {c for c in where_cols if c != "company_id"}
        if requires_company_scope and not non_tenant_filters:
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
                        content=self._filter_prompt_message(table)
                    )
                ],
            }
        return {"sql_query": sql}
