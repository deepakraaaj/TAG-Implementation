import logging
import json
from sqlalchemy import text
from app.workflow.state import AgentState
from app.config import get_settings
from app.services.schema_service import SchemaService
from app.services.toon import toon

logger = logging.getLogger(__name__)
settings = get_settings()

class ExecuteSQLNode:
    def __init__(self):
        self.schema_service = SchemaService()

    async def run(self, state: AgentState):
        """
        Executes the SQL query.
        """
        logger.info("Entering execute_sql_node")
        if state.get("error"):
            return {} # Skip execution if validation failed
            
        sql = state.get("sql_query")
        if not sql or sql == "SKIP":
             return {}

        try:
             # --- DB Context for Execution ---
             metadata = state.get("metadata", {})
             db_url = metadata.get("db_connection_string") or settings.DATABASE_URL
             
             # --- SQL Auto-Fixer (Poor man's parameterization) ---
             # If stubbornly LLM uses '?', try to replace with known IDs
             if "?" in sql:
                 logger.warning(f"Detected placeholders in SQL: {sql}. Attempting auto-fix.")
                 # Try common candidates
                 if "user_id" in sql.lower() and metadata.get("user_id"):
                     sql = sql.replace("?", str(metadata["user_id"]), 1)
                 elif "company_id" in sql.lower() and metadata.get("company_id"):
                     sql = sql.replace("?", str(metadata["company_id"]), 1)
                 elif metadata.get("user_id"): # Fallback replace first ? with user_id
                     sql = sql.replace("?", str(metadata["user_id"]), 1)
                 logger.info(f"Fixed SQL: {sql}")

             engine = self.schema_service.get_engine_for_url(db_url)
             
             with engine.connect() as conn:
                result = conn.execute(text(sql))
                
                if result.returns_rows:
                    rows = [dict(row) for row in result.mappings()]
                else:
                    conn.commit()
                    rows = [{"status": "success", "rows_affected": result.rowcount}]
                 
                # Smart Pagination Logic
                # 1. Total Count (Try to get true count ignoring LIMIT)
                total_count = len(rows)
                try:
                    # Heuristic to strip LIMIT for count query
                    import re
                    count_sql = re.sub(r"(?i)\s+LIMIT\s+\d+", "", sql)
                    count_sql = re.sub(r"(?i)\s+ORDER\s+BY.*?(?=(LIMIT|$))", "", count_sql, flags=re.DOTALL)
                    count_sql = f"SELECT COUNT(*) FROM ({count_sql}) as subquery"
                    
                    # Only run count if original query seems to be a SELECT
                    if sql.strip().upper().startswith("SELECT"):
                        count_result = conn.execute(text(count_sql))
                        total_count = count_result.scalar()
                except Exception as e:
                    logger.warning(f"Could not calculate total count: {e}")
                    # Fallback to len(rows)

                # 2. Pagination Window
                metadata = state.get("metadata", {})
                page = int(metadata.get("page", 1))
                limit = int(metadata.get("limit", 15)) # Default to 15 as requested
                
                start_idx = (page - 1) * limit
                end_idx = start_idx + limit
                
                paginated_rows = rows[start_idx:end_idx]
                
                # --- Toon Compression Metrics ---
                # We calculate savings based on the FULL result set to show potential impact
                toon_result = toon.encode(rows)
                
                return {
                    "sql_result": json.dumps(rows, default=str), 
                    "row_count": total_count,
                    "rows_preview": paginated_rows,
                    "toon_data": toon_result["meta"], # Only passing meta for display
                    "error": None,
                    "retry_count": 0 
                } 

        except Exception as e:
            logger.error(f"SQL Execution failed: {e}")
            return {
                "error": str(e),
                "retry_count": state.get("retry_count", 0) + 1
            }
