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
        
        for column in ["status", "facility_status"]:
            raw_value = serialized.get(column)
            if raw_value is None:
                continue
                
            # Convert string digits to int
            if isinstance(raw_value, str) and raw_value.isdigit():
                raw_value = int(raw_value)
            
            # Get label from domain
            if isinstance(raw_value, int):
                label = domain.get_enum_label(column, raw_value)
                if label != raw_value:  # Only update if mapping exists
                    serialized[column] = label
        
        return serialized

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
                    count = len(rows)
                else:
                    conn.commit()
                    count = int(result.rowcount or 0)
                    rows = [{"status": "ok", "rows_affected": count}]

            return {
                "sql_result": json.dumps(rows, default=str),
                "row_count": count,
                "rows_preview": rows[:20],
                "error": None,
            }
        except Exception as exc:
            return {"error": str(exc)}
