import sqlglot
from sqlglot import exp
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class SQLValidatorService:
    def __init__(self, allowed_tables: List[str] = None):
        # In a real scenario, allowed_tables should be populated dynamically or from config
        self.allowed_tables = set(allowed_tables) if allowed_tables else None
        # Removed exp.Insert and exp.Update to allow actions
        self.forbidden_commands = {exp.Drop, exp.Delete, exp.Alter, exp.Create}

    def _extract_table_alias(self, table_node: exp.Table) -> str:
        """Best-effort alias extraction compatible with multiple sqlglot versions."""
        alias = getattr(table_node, "alias_or_name", "")
        if alias:
            return alias

        alias_expr = getattr(table_node, "alias", None)
        if isinstance(alias_expr, str):
            return alias_expr
        if alias_expr is not None:
            return getattr(alias_expr, "name", "") or ""

        return ""

    def _validate_columns(self, parsed: exp.Expression, table_columns: Dict[str, Set[str]]) -> bool:
        """
        Validate qualified column references (e.g., u.email_id) against inspected table schema.
        This catches hallucinated fields before execution.
        """
        alias_to_table: Dict[str, str] = {}

        for table in parsed.find_all(exp.Table):
            table_name = table.name
            if table_name:
                alias_to_table[table_name] = table_name

            alias = self._extract_table_alias(table)
            if alias:
                alias_to_table[alias] = table_name

        invalid_columns: List[str] = []
        for column in parsed.find_all(exp.Column):
            qualifier = column.table
            column_name = column.name

            # Only validate qualified columns to avoid false positives with projection aliases.
            if not qualifier or not column_name:
                continue

            table_name = alias_to_table.get(qualifier)
            if not table_name:
                continue

            allowed_cols = table_columns.get(table_name)
            if allowed_cols is not None and column_name not in allowed_cols:
                invalid_columns.append(f"{qualifier}.{column_name}")

        if invalid_columns:
            logger.warning("Unknown columns detected in SQL: %s", ", ".join(invalid_columns))
            return False

        return True

    def _validate_unique_table_aliases(self, parsed: exp.Expression) -> bool:
        """
        Reject queries that reuse the same table alias for multiple tables, which
        commonly leads to MySQL 1066 (Not unique table/alias).
        """
        seen_aliases: Set[str] = set()
        duplicate_aliases: List[str] = []

        for table in parsed.find_all(exp.Table):
            alias = self._extract_table_alias(table)
            if not alias:
                continue
            alias_key = alias.lower()
            if alias_key in seen_aliases:
                duplicate_aliases.append(alias)
            else:
                seen_aliases.add(alias_key)

        if duplicate_aliases:
            logger.warning("Duplicate table aliases detected: %s", ", ".join(duplicate_aliases))
            return False

        return True

    def validate_sql(self, sql: str, table_columns: Optional[Dict[str, Set[str]]] = None) -> bool:
        """
        Validates the SQL query:
        1. Parses the SQL.
        2. Checks for forbidden commands (DROP, DELETE, etc.).
        3. Checks if tables accessed are in the allow-list.
        4. Optionally validates column references against live schema.
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

        if not self._validate_unique_table_aliases(parsed):
            return False

        # Check tables if allowed_tables is set
        if self.allowed_tables:
            tables = [t.name for t in parsed.find_all(exp.Table)]
            for table in tables:
                if table not in self.allowed_tables:
                    logger.warning(f"Access to forbidden table: {table}")
                    return False

        if table_columns is not None and not self._validate_columns(parsed, table_columns):
            return False

        return True

    def get_tables(self, sql: str) -> List[str]:
        try:
            parsed = sqlglot.parse_one(sql)
            return [t.name for t in parsed.find_all(exp.Table)]
        except Exception:
            return []
