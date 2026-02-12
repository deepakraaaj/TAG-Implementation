import logging
from app.workflow.state import AgentState
from app.services.sql_validator import SQLValidatorService
from app.services.schema_service import SchemaService
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ValidateSQLNode:
    def __init__(self):
        self.sql_validator = SQLValidatorService(allowed_tables=None)
        self.schema_service = SchemaService()

    async def run(self, state: AgentState):
        """
        Validates the generated SQL.
        """
        logger.info("Entering validate_node")
        sql = state.get("sql_query")

        if not sql or sql == "SKIP":
            return {"error": None}

        logger.info(f"Generated SQL: {sql}")

        metadata = state.get("metadata", {})
        db_url = metadata.get("db_connection_string") or settings.DATABASE_URL

        table_columns = None
        try:
            tables = self.sql_validator.get_tables(sql)
            if tables:
                table_columns = self.schema_service.get_table_columns(list(dict.fromkeys(tables)), db_url=db_url)
        except Exception as e:
            logger.warning(f"Schema-aware validation fallback due to error: {e}")

        is_valid = self.sql_validator.validate_sql(sql, table_columns=table_columns)

        if not is_valid:
            logger.warning(f"SQL Validation failed for query: {sql}")
            return {
                "error": "SQL query failed validation (unsafe command, forbidden table, or unknown column).",
                "retry_count": state.get("retry_count", 0) + 1,
            }

        return {"error": None}
