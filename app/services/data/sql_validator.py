import sqlglot
from sqlglot import exp
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class SQLValidatorService:
    SYSTEM_TABLE_PREFIXES = ("information_schema.", "mysql.", "performance_schema.", "sys.")

    # Tables that must never be queried, regardless of the per-app allow-list.
    # Matched if the (normalized) table name contains any of these tokens, so
    # api_key, random_password_generator, user_password_reset_token, etc. are
    # all caught. `user` and other operational tables are unaffected.
    SENSITIVE_TABLE_TOKENS = (
        "password",
        "secret",
        "credential",
        "api_key",
        "apikey",
        "private_key",
        "access_token",
        "refresh_token",
    )

    # Column names that must never be returned. Strong tokens match anywhere in
    # the name; word tokens match a whole underscore-separated part only (so
    # "pin" doesn't trip on "shipping").
    SENSITIVE_COLUMN_SUBSTRINGS = (
        "password",
        "passwd",
        "secret",
        "apikey",
        "api_key",
        "accesstoken",
        "access_token",
        "refreshtoken",
        "refresh_token",
        "privatekey",
        "private_key",
        "secretkey",
        "secret_key",
        "credential",
    )
    SENSITIVE_COLUMN_PARTS = (
        "pwd",
        "token",
        "salt",
        "otp",
        "totp",
        "mfa",
        "cvv",
        "ssn",
        "pin",
        "hash",
    )

    @classmethod
    def is_sensitive_table(cls, name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return bool(normalized) and any(tok in normalized for tok in cls.SENSITIVE_TABLE_TOKENS)

    @classmethod
    def is_sensitive_column(cls, name: str) -> bool:
        normalized = str(name or "").strip().lower()
        if not normalized:
            return False
        if any(tok in normalized for tok in cls.SENSITIVE_COLUMN_SUBSTRINGS):
            return True
        parts = set(normalized.replace(".", "_").split("_"))
        return bool(parts & set(cls.SENSITIVE_COLUMN_PARTS))

    def __init__(
        self,
        allowed_tables: List[str] = None,
        allow_mutations: bool = True,
        protected_tables: List[str] = None,
        require_select_where: bool = True,
    ):
        # In a real scenario, allowed_tables should be populated dynamically or from config
        self.allowed_tables = {str(t).strip().lower() for t in (allowed_tables or []) if str(t).strip()} or None
        self.protected_tables = {str(t).strip().lower() for t in (protected_tables or []) if str(t).strip()} or None
        self.allow_mutations = bool(allow_mutations)
        self.require_select_where = bool(require_select_where)
        self.forbidden_commands = {exp.Drop, exp.Delete, exp.Alter, exp.Create}
        self.allowed_top_level = (exp.Select, exp.Insert, exp.Update)

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

    @classmethod
    def _select_has_where(cls, parsed: exp.Expression) -> bool:
        """
        Enforce filtered reads: plain SELECT statements must include WHERE.
        """
        if not isinstance(parsed, exp.Select):
            return True
        if parsed.args.get("where") is not None:
            return True

        # Allow wrapped aggregate queries like:
        # SELECT COUNT(*) FROM (SELECT ... WHERE company_id=...) count_rows
        # without weakening the general "require WHERE" policy for normal reads.
        from_expr = parsed.args.get("from") or parsed.args.get("from_")
        inner_source = from_expr.this if isinstance(from_expr, exp.From) else None
        if isinstance(inner_source, exp.Subquery) and isinstance(inner_source.this, exp.Select):
            return cls._select_has_where(inner_source.this)

        return False

    @staticmethod
    def _update_has_where(parsed: exp.Expression) -> bool:
        if not isinstance(parsed, exp.Update):
            return True
        return parsed.args.get("where") is not None

    @classmethod
    def _is_system_table(cls, table: str) -> bool:
        normalized = str(table or "").strip().lower()
        if not normalized:
            return False
        return any(normalized.startswith(prefix) for prefix in cls.SYSTEM_TABLE_PREFIXES)

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    def validate_sql(
        self,
        sql: str,
        table_columns: Optional[Dict[str, Set[str]]] = None,
        allow_mutations_override: Optional[bool] = None,
        allowed_tables_override: Optional[List[str]] = None,
        protected_tables_override: Optional[List[str]] = None,
        require_select_where_override: Optional[bool] = None,
    ) -> bool:
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

        if not isinstance(parsed, self.allowed_top_level):
            logger.warning("Unsupported SQL statement type: %s", type(parsed).__name__)
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

        allow_mutations = self.allow_mutations
        if allow_mutations_override is not None:
            allow_mutations = bool(allow_mutations_override)
        if isinstance(parsed, (exp.Insert, exp.Update)) and not allow_mutations:
            logger.warning("Mutation SQL rejected by policy: %s", parsed.sql())
            return False

        require_select_where = self.require_select_where
        if require_select_where_override is not None:
            require_select_where = bool(require_select_where_override)

        if require_select_where and not self._select_has_where(parsed):
            logger.warning("Rejected unfiltered SELECT (missing WHERE): %s", parsed.sql())
            return False

        if not self._update_has_where(parsed):
            logger.warning("Rejected unsafe UPDATE without WHERE: %s", parsed.sql())
            return False

        # Check tables if allowed_tables is set
        table_refs: List[str] = []
        table_names: List[str] = []
        for table_node in parsed.find_all(exp.Table):
            table_name = str(table_node.name or "").strip()
            table_db = str(getattr(table_node, "db", "") or "").strip()
            table_names.append(table_name)
            qualified = f"{table_db}.{table_name}" if table_db else table_name
            table_refs.append(qualified)

        protected_tables = self.protected_tables
        if protected_tables_override is not None:
            protected_tables = {
                str(value).strip().lower()
                for value in protected_tables_override
                if str(value).strip()
            } or None

        for table_ref in table_refs:
            if self._is_system_table(table_ref):
                logger.warning("Access to protected system table blocked: %s", table_ref)
                return False

        # Block sensitive tables (api keys, passwords, credentials) outright.
        for table in table_names:
            if self.is_sensitive_table(table):
                logger.warning("Access to sensitive table blocked: %s", table)
                return False

        # Block any explicit reference to a sensitive column.
        for column in parsed.find_all(exp.Column):
            if self.is_sensitive_column(column.name):
                logger.warning("Access to sensitive column blocked: %s", column.name)
                return False

        if protected_tables:
            for table in table_names:
                if str(table).lower() in protected_tables:
                    logger.warning("Access to protected table blocked: %s", table)
                    return False

        allowed_tables = self.allowed_tables
        if allowed_tables_override is not None:
            allowed_tables = {
                str(value).strip().lower()
                for value in allowed_tables_override
                if str(value).strip()
            } or None

        if allowed_tables:
            for table in table_names:
                if str(table).lower() not in allowed_tables:
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
