import logging
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class QueryRefinerService:
    """
    Centralizes heuristic logic for SQL refinement, security guards, and auto-correction.
    """
    
    def apply_ironclad_heuristics(self, sql_query: str, resolved_persons: Dict[str, List[int]], company_id: Optional[int]) -> str:
        """
        Applies strict rules to the SQL query to ensure safety and accuracy.
        1. Inject Person IDs if resolved.
        2. Ensure Company ID is present.
        """
        if not sql_query or "SELECT" not in sql_query.upper():
            return sql_query

        # 1. Person Identity Guard - REMOVED strictly forcing column name 'assigned_user_id' because it breaks queries on other tables.
        # We now rely on the Prompt (Rule 2) which is given the resolved IDs.
        pass

        # 2. Company Security Guard
        if company_id:
             # Check if company_id is already in the query string (simple text check)
             if str(company_id) not in sql_query:
                logger.warning("Generated SQL missing company_id security filter. Self-correcting.")
                if "WHERE" in sql_query.upper():
                    sql_query = re.sub(r"(?i)WHERE\s+", f"WHERE company_id = {company_id} AND ", sql_query, count=1)
                else:
                    sql_query += f" WHERE company_id = {company_id}"
        
        return sql_query

    def auto_fix_sql(self, sql: str, metadata: Dict[str, Any]) -> str:
        """
        Attempts to fix common SQL issues like parameter placeholders '?'
        """
        if "?" in sql:
            logger.warning(f"Detected placeholders in SQL: {sql}. Attempting auto-fix.")
            # Try valid replacements based on context
            if "user_id" in sql.lower() and metadata.get("user_id"):
                sql = sql.replace("?", str(metadata["user_id"]), 1)
            elif "company_id" in sql.lower() and metadata.get("company_id"):
                 sql = sql.replace("?", str(metadata["company_id"]), 1)
            elif metadata.get("user_id"):
                 sql = sql.replace("?", str(metadata["user_id"]), 1)
            logger.info(f"Fixed SQL: {sql}")
        
        return sql
