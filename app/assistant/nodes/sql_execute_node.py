import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Dict

from sqlalchemy import text

from app.config import get_settings
from app.services.schema_service import SchemaService
from app.domains.registry import DomainRegistry

settings = get_settings()


class SQLExecuteNode:
    def __init__(self):
        self.schema = SchemaService()
        self.domain = DomainRegistry.get_current_domain()

    @staticmethod
    def _serialize_cell(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, time):
            return value.strftime("%H:%M:%S")
        if isinstance(value, Decimal):
            return float(value)
        return value

    @classmethod
    def _serialize_row(cls, row: Dict):
        serialized = {k: cls._serialize_cell(v) for k, v in dict(row or {}).items()}
        
        # Use domain registry for enum label conversion
        domain = DomainRegistry.get_current_domain()

        enum_columns = set()
        getter = getattr(domain, "enum_columns", None)
        if callable(getter):
            try:
                enum_columns = {str(c or "").strip().lower() for c in getter() if str(c or "").strip()}
            except Exception:
                enum_columns = set()
        if not enum_columns:
            enum_columns = {"status", "facility_status"}

        for key, value in list(serialized.items()):
            column = str(key or "").strip()
            if not column:
                continue
            normalized_column = column.split(".")[-1].strip().lower()
            if normalized_column not in enum_columns:
                continue

            raw_value = value
            if isinstance(raw_value, str) and raw_value.isdigit():
                raw_value = int(raw_value)

            if isinstance(raw_value, int):
                label = domain.get_enum_label(normalized_column, raw_value)
                if label != raw_value:
                    serialized[key] = label
        
        return serialized

    @staticmethod
    def _extract_window_total_count(rows: list[Dict]) -> int | None:
        if not rows:
            return None
        first = dict(rows[0] or {})
        value = first.get("_total_count")
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except Exception:
            return None

    @staticmethod
    def _strip_window_total_count(rows: list[Dict]) -> list[Dict]:
        cleaned: list[Dict] = []
        for row in rows or []:
            item = dict(row or {})
            item.pop("_total_count", None)
            cleaned.append(item)
        return cleaned

    async def run(self, state: Dict) -> Dict:
        if state.get("error"):
            return {}

        sql = state.get("sql_query")
        if not sql or sql == "SKIP":
            return {}

        metadata = state.get("metadata", {})
        db_url = metadata.get("db_connection_string") or settings.DATABASE_URL

        try:
            engine = self.schema.get_engine_for_url(db_url)
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                if result.returns_rows:
                    rows = [self._serialize_row(dict(row)) for row in result.mappings().all()]
                    total_records = self._extract_window_total_count(rows)
                    if total_records is not None:
                        rows = self._strip_window_total_count(rows)
                    count = len(rows)
                else:
                    conn.commit()
                    count = int(result.rowcount or 0)
                    rows = [{"status": "ok", "rows_affected": count}]
                    total_records = None

            return {
                "sql_result": json.dumps(rows, default=str),
                "row_count": count,
                "rows_preview": rows[:20],
                "total_records": total_records,
                "error": None,
            }
        except Exception as exc:
            return {"error": str(exc)}
