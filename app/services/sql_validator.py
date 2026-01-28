import sqlglot
from sqlglot import exp
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class SQLValidatorService:
    def __init__(self, allowed_tables: List[str] = None):
        # In a real scenario, allowed_tables should be populated dynamically or from config
        self.allowed_tables = set(allowed_tables) if allowed_tables else None
        # Removed exp.Insert and exp.Update to allow actions
        self.forbidden_commands = {exp.Drop, exp.Delete, exp.Alter, exp.Create}

    def validate_sql(self, sql: str) -> bool:
        """
        Validates the SQL query:
        1. Parses the SQL.
        2. Checks for forbidden commands (DROP, DELETE, etc.).
        3. Checks if tables accessed are in the allow-list.
        """
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception as e:
            logger.error(f"Failed to parse SQL: {e}")
            return False

        # Check for forbidden commands
        if type(parsed) in self.forbidden_commands:
            logger.warning(f"Forbidden command detected: {parsed.sql()}")
            return False
            
        # Recursive check for subqueries/CTEs if needed, but sqlglot's valid check might be simpler for top-level
        # Let's walk the AST for forbidden commands anywhere
        for node in parsed.walk():
            if type(node) in self.forbidden_commands:
                 logger.warning(f"Forbidden command detected in sub-clause: {node.sql()}")
                 return False

        # Check tables if allowed_tables is set
        if self.allowed_tables:
            tables = [t.name for t in parsed.find_all(exp.Table)]
            for table in tables:
                if table not in self.allowed_tables:
                    logger.warning(f"Access to forbidden table: {table}")
                    return False

        return True

    def get_tables(self, sql: str) -> List[str]:
        try:
             parsed = sqlglot.parse_one(sql)
             return [t.name for t in parsed.find_all(exp.Table)]
        except:
            return []
