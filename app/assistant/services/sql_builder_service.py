import json
import os
import re
from typing import Any, Dict, Tuple

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

from app.assistant.services.manifest_catalog import ManifestCatalog

settings = get_settings()


class SQLBuilderService:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )
        self.catalog = ManifestCatalog()

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

    def build_insert(self, table: str, fields: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
        allowed = self.catalog.important_columns(table)
        normalized = {}
        for k, v in fields.items():
            ident = self._safe_ident(k)
            if not ident:
                continue
            if allowed and ident not in allowed:
                continue
            normalized[ident] = v

        if "company_id" in allowed and company_id and "company_id" not in normalized:
            normalized["company_id"] = company_id

        if not normalized:
            return "", "No valid fields found for insert."

        cols = ", ".join(normalized.keys())
        vals = ", ".join(self._safe_value(v) for v in normalized.values())
        return f"INSERT INTO {table} ({cols}) VALUES ({vals});", ""

    def build_update(self, table: str, fields: Dict[str, Any], company_id: Any) -> Tuple[str, str]:
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
            updates[ident] = v

        if not updates:
            return "", "Update requires at least one field to change."

        set_clause = ", ".join(f"{k}={self._safe_value(v)}" for k, v in updates.items())
        where = f"id={self._safe_value(record_id)}"
        if "company_id" in allowed and company_id:
            where += f" AND company_id={self._safe_value(company_id)}"
        return f"UPDATE {table} SET {set_clause} WHERE {where};", ""

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
                attempts=2,
                backoff_seconds=0.3,
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
