from typing import Dict

from app.config import get_settings
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

settings = get_settings()


class SQLValidateNode:
    def __init__(self):
        self.validator = SQLValidatorService(allowed_tables=None)
        self.schema = SchemaService()

    async def run(self, state: Dict) -> Dict:
        sql = state.get("sql_query")
        if not sql or sql == "SKIP":
            return {"error": None}

        metadata = state.get("metadata", {})
        db_url = metadata.get("db_connection_string") or settings.DATABASE_URL

        table_columns = None
        try:
            tables = self.validator.get_tables(sql)
            if tables:
                table_columns = self.schema.get_table_columns(list(dict.fromkeys(tables)), db_url=db_url)
        except Exception:
            table_columns = None

        if not self.validator.validate_sql(sql, table_columns=table_columns):
            return {"error": "SQL failed safety validation."}

        return {"error": None}
