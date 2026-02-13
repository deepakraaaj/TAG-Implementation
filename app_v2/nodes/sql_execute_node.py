import json
from typing import Dict

from sqlalchemy import text

from app.config import get_settings
from app.services.schema_service import SchemaService

settings = get_settings()


class SQLExecuteNode:
    def __init__(self):
        self.schema = SchemaService()

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
                    rows = [dict(row) for row in result.mappings().all()]
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
