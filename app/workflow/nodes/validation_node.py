import logging
from app.workflow.state import AgentState
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)

class ValidateSQLNode:
    def __init__(self):
        self.sql_validator = SQLValidatorService(allowed_tables=None) 

    async def run(self, state: AgentState):
        """
        Validates the generated SQL.
        """
        logger.info("Entering validate_node")
        sql = state.get("sql_query")
        
        if not sql or sql == "SKIP":
            return {"error": None}

        logger.info(f"Generated SQL: {sql}")
        is_valid = self.sql_validator.validate_sql(sql)
        
        if not is_valid:
            logger.warning(f"SQL Validation failed for query: {sql}")
            return {
                "error": "SQL Query violated safety policy (destructive command or forbidden table).",
                "retry_count": state.get("retry_count", 0) + 1
            }
            
        return {"error": None}
