import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Dict

from sqlalchemy import text

from app.config import get_settings
from app.services.schema_service import SchemaService

settings = get_settings()


class SQLExecuteNode:
    TASK_STATUS_LABELS = {
        0: "Pending",
        1: "In Progress",
        2: "Completed",
        3: "Overdue",
    }
    FACILITY_STATUS_LABELS = {
        0: "Assigned",
        1: "In Progress",
        2: "Overdue",
        3: "Delay In Progress",
        4: "Completed",
    }

    def __init__(self):
        self.schema = SchemaService()

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
        status_raw = serialized.get("status")
        facility_status_raw = serialized.get("facility_status")

        if isinstance(status_raw, str) and status_raw.isdigit():
            status_raw = int(status_raw)
        if isinstance(facility_status_raw, str) and facility_status_raw.isdigit():
            facility_status_raw = int(facility_status_raw)

        if isinstance(status_raw, int) and status_raw in cls.TASK_STATUS_LABELS:
            serialized["status"] = cls.TASK_STATUS_LABELS[status_raw]
        if isinstance(facility_status_raw, int) and facility_status_raw in cls.FACILITY_STATUS_LABELS:
            serialized["facility_status"] = cls.FACILITY_STATUS_LABELS[facility_status_raw]
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
