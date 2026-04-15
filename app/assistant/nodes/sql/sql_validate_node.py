from datetime import datetime, timedelta
import re
from typing import Any, Dict, Optional, Set

import sqlglot
from sqlglot import exp

from app.config import get_settings
from app.domains.registry import DomainRegistry
from app.services.data.sql_validator import SQLValidatorService

settings = get_settings()


class SQLValidateNode:
    def __init__(
        self,
        validator: SQLValidatorService | None = None,
        schema_service: Any | None = None,
        metrics_service: Any | None = None,
        allowed_mutation_roles: Optional[Set[str]] = None,
        require_explicit_mutation_permission: Optional[bool] = None,
    ):
        if validator is None:
            validator = SQLValidatorService(allowed_tables=None)

        if schema_service is None:
            try:
                from app.services.data.schema_service import SchemaService

                schema_service = SchemaService()
            except Exception:
                schema_service = type(
                    "_NoopSchema",
                    (),
                    {
                        "get_table_columns": staticmethod(lambda _tables, db_url=None: {}),
                        "get_table_column_types": staticmethod(lambda _tables, db_url=None: {}),
                    },
                )()

        if metrics_service is None:
            from app.services.observability.metrics_service import MetricsService

            metrics_service = MetricsService()

        if allowed_mutation_roles is None:
            raw_roles = str(getattr(settings, "MUTATION_ALLOWED_ROLES", "admin,superadmin"))
            allowed_mutation_roles = {
                str(role).strip().lower()
                for role in raw_roles.split(",")
                if str(role).strip()
            }

        if require_explicit_mutation_permission is None:
            require_explicit_mutation_permission = bool(
                getattr(settings, "MUTATION_REQUIRE_EXPLICIT_PERMISSION", True)
            )

        self.validator = validator
        self.schema = schema_service
        self.metrics = metrics_service
        self.allowed_mutation_roles = {
            str(role).strip().lower()
            for role in (allowed_mutation_roles or set())
            if str(role).strip()
        }
        self.require_explicit_mutation_permission = bool(require_explicit_mutation_permission)

    async def run(self, state: Dict) -> Dict:
        sql = state.get("sql_query")
        if not sql or sql == "SKIP":
            return {"error": None}

        metadata = state.get("metadata", {})
        db_url = metadata.get("db_connection_string") or settings.DATABASE_URL
        is_mutation = self._is_mutation_sql(sql)
        allow_mutations_override = self._mutation_policy_override(metadata, is_mutation=is_mutation)
        if is_mutation and self._is_safe_task_status_update(sql, metadata):
            allow_mutations_override = True
        if is_mutation and allow_mutations_override is False:
            self.metrics.record_mutation_denied(reason="role_or_policy")
            return {"error": "Mutation not allowed for current role/policy."}

        table_columns = None
        table_column_types = None
        try:
            tables = self.validator.get_tables(sql)
            if tables:
                unique_tables = list(dict.fromkeys(tables))
                table_columns = self.schema.get_table_columns(unique_tables, db_url=db_url)
                table_column_types = self.schema.get_table_column_types(unique_tables, db_url=db_url)
        except Exception:
            table_columns = None
            table_column_types = None

        sql = self._rewrite_date_only_equals_for_datetimes(sql, table_column_types)

        try:
            is_valid = self.validator.validate_sql(
                sql,
                table_columns=table_columns,
                allow_mutations_override=allow_mutations_override,
                allowed_tables_override=self._allowed_tables(metadata),
                protected_tables_override=self._protected_tables(metadata),
                require_select_where_override=self._require_select_where(metadata),
            )
        except TypeError:
            # Backward-compatible path for older validator stubs used in tests.
            is_valid = self.validator.validate_sql(
                sql,
                table_columns=table_columns,
            )

        if not is_valid:
            return {"error": "SQL failed safety validation."}

        return {"error": None, "sql_query": sql}

    @staticmethod
    def _parse_allow_mutations_flag(metadata: Dict) -> Optional[bool]:
        if not isinstance(metadata, dict):
            return None
        if "allow_mutations" not in metadata:
            return None
        value = metadata.get("allow_mutations")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _list_override(metadata: Dict, key: str) -> Optional[list[str]]:
        if not isinstance(metadata, dict):
            return None
        payload = metadata.get(key)
        if payload is None:
            return None
        if not isinstance(payload, list):
            return None
        values = [str(item).strip() for item in payload if str(item).strip()]
        return values

    @classmethod
    def _allowed_tables(cls, metadata: Dict) -> Optional[list[str]]:
        return cls._list_override(metadata, "allowed_tables")

    @classmethod
    def _protected_tables(cls, metadata: Dict) -> Optional[list[str]]:
        return cls._list_override(metadata, "protected_tables")

    @staticmethod
    def _require_select_where(metadata: Dict) -> Optional[bool]:
        if not isinstance(metadata, dict):
            return None
        if "require_select_where" not in metadata:
            return None
        value = metadata.get("require_select_where")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _is_mutation_sql(sql: str) -> bool:
        text = str(sql or "").strip().upper()
        return text.startswith("INSERT") or text.startswith("UPDATE")

    @staticmethod
    def _normalized_role(metadata: Dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        for key in ("user_role", "role", "userRole"):
            value = str(metadata.get(key, "") or "").strip().lower()
            if value:
                return value
        return ""

    def _mutation_policy_override(self, metadata: Dict, is_mutation: bool = False) -> Optional[bool]:
        flag = self._parse_allow_mutations_flag(metadata)
        if not is_mutation:
            return flag
        role = self._normalized_role(metadata)
        role_allowed = role in self.allowed_mutation_roles
        if self.require_explicit_mutation_permission and flag is not True:
            return False
        if flag is False:
            return False
        if not role_allowed:
            return False
        return True

    @staticmethod
    def _primary_task_table() -> str:
        try:
            domain = DomainRegistry.get_current_domain()
            cfg = domain.get_entity_behavior_config()
            table = str(cfg.get("primary_table", "")).strip()
            if table:
                return table.lower()
        except Exception:
            pass
        return "task_transaction"

    @classmethod
    def _task_status_update_tables(cls) -> Set[str]:
        tables = {"task_transaction"}
        primary_table = str(cls._primary_task_table() or "").strip().lower()
        if primary_table:
            tables.add(primary_table)
        return tables

    @staticmethod
    def _assignment_column_names(parsed: exp.Update) -> Set[str]:
        names: Set[str] = set()
        for assignment in parsed.args.get("expressions") or []:
            candidate = assignment
            nested = getattr(candidate, "this", None)
            if isinstance(candidate, exp.SetItem) and isinstance(nested, exp.Expression):
                candidate = nested
            column = candidate.this if isinstance(getattr(candidate, "this", None), exp.Column) else None
            if column is None:
                continue
            name = str(column.name or "").strip().lower()
            if name:
                names.add(name)
        return names

    @staticmethod
    def _where_column_names(where: exp.Expression | None) -> Set[str]:
        names: Set[str] = set()
        if where is None:
            return names
        for node in where.find_all(exp.EQ):
            for candidate in (node.this, node.expression):
                if not isinstance(candidate, exp.Column):
                    continue
                name = str(candidate.name or "").strip().lower()
                if name:
                    names.add(name)
        return names

    @classmethod
    def _is_safe_task_status_update(cls, sql: str, metadata: Dict) -> bool:
        if cls._parse_allow_mutations_flag(metadata) is not True:
            return False
        scope = str((metadata or {}).get("mutation_scope", "") or "").strip().lower()
        if scope != "task_status_update":
            return False
        role = cls._normalized_role(metadata)
        if role not in {"", "user"}:
            return False
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception:
            return False
        if not isinstance(parsed, exp.Update):
            return False
        table_node = parsed.this if isinstance(parsed.this, exp.Table) else None
        table_name = str(table_node.name or "").strip().lower() if table_node is not None else ""
        if table_name not in cls._task_status_update_tables():
            return False
        assignment_names = cls._assignment_column_names(parsed)
        if "status" not in assignment_names:
            return False
        if not assignment_names.issubset({"status", "updated_by"}):
            return False
        where_names = cls._where_column_names(parsed.args.get("where"))
        return "id" in where_names

    @staticmethod
    def _extract_table_alias(table_node: exp.Table) -> str:
        alias = getattr(table_node, "alias_or_name", "")
        if alias:
            return alias
        alias_expr = getattr(table_node, "alias", None)
        if isinstance(alias_expr, str):
            return alias_expr
        if alias_expr is not None:
            return getattr(alias_expr, "name", "") or ""
        return ""

    @staticmethod
    def _is_date_literal(value: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))

    @classmethod
    def _datetime_columns_from_schema(
        cls,
        parsed: exp.Expression,
        table_column_types: Optional[Dict[str, Dict[str, str]]],
    ) -> Set[str]:
        if not table_column_types:
            return set()

        alias_to_table: Dict[str, str] = {}
        for table in parsed.find_all(exp.Table):
            table_name = table.name
            if table_name:
                alias_to_table[table_name] = table_name
            alias = cls._extract_table_alias(table)
            if alias and table_name:
                alias_to_table[alias] = table_name

        eligible: Set[str] = set()
        for column in parsed.find_all(exp.Column):
            col_name = str(column.name or "").strip()
            if not col_name:
                continue

            qualifier = str(column.table or "").strip()
            if qualifier:
                table_name = alias_to_table.get(qualifier)
                col_type = ((table_column_types.get(table_name) or {}).get(col_name) or "").upper()
                if "DATETIME" in col_type or "TIMESTAMP" in col_type:
                    eligible.add(col_name.lower())
                continue

            for table_name in set(alias_to_table.values()):
                col_type = ((table_column_types.get(table_name) or {}).get(col_name) or "").upper()
                if "DATETIME" in col_type or "TIMESTAMP" in col_type:
                    eligible.add(col_name.lower())
                    break

        return eligible

    @classmethod
    def _rewrite_date_only_equals_for_datetimes(
        cls,
        sql: str,
        table_column_types: Optional[Dict[str, Dict[str, str]]],
    ) -> str:
        """
        Rewrite patterns like `scheduled_date = '2026-02-18'` to a full-day range
        when the column is DATETIME/TIMESTAMP.
        """
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception:
            return sql

        eligible_datetime_columns = cls._datetime_columns_from_schema(parsed, table_column_types)
        if not eligible_datetime_columns:
            return sql

        rewritten_any = False

        def _rewrite(node: exp.Expression) -> exp.Expression:
            nonlocal rewritten_any
            if not isinstance(node, exp.EQ):
                return node

            left = node.this
            right = node.expression
            col = left if isinstance(left, exp.Column) else (right if isinstance(right, exp.Column) else None)
            lit = right if isinstance(right, exp.Literal) else (left if isinstance(left, exp.Literal) else None)
            if col is None or lit is None or not lit.is_string:
                return node

            col_name = str(col.name or "").strip().lower()
            lit_value = str(lit.this or "").strip()
            if col_name not in eligible_datetime_columns or not cls._is_date_literal(lit_value):
                return node

            try:
                start = datetime.strptime(lit_value, "%Y-%m-%d")
            except ValueError:
                return node
            end = start + timedelta(days=1)
            start_text = start.strftime("%Y-%m-%d 00:00:00")
            end_text = end.strftime("%Y-%m-%d 00:00:00")

            rewritten_any = True
            return exp.and_(
                exp.GTE(this=col.copy(), expression=exp.Literal.string(start_text)),
                exp.LT(this=col.copy(), expression=exp.Literal.string(end_text)),
            )

        rewritten = parsed.transform(_rewrite)
        if not rewritten_any:
            return sql
        return rewritten.sql(dialect="mysql")
