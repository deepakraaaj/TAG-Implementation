import json
import logging
from datetime import date, datetime, time
from decimal import Decimal
from time import perf_counter
from typing import Any, Callable, Dict, List

from sqlalchemy import text

from app.config import get_settings
from app.db import dialect
from app.services.data.sql_validator import SQLValidatorService
from app.services.interfaces import SchemaGateway
from app.services.observability.nl2sql_audit import audit_nl2sql

settings = get_settings()
logger = logging.getLogger(__name__)


class SQLExecuteNode:
    def __init__(
        self,
        schema_service: SchemaGateway,
        domain_provider: Callable[[], Any],
        semantic_retriever: Any = None,
        auto_learn_on_success: bool | None = None,
    ):
        self.schema = schema_service
        self.domain_provider = domain_provider
        self.semantic_retriever = semantic_retriever
        self.auto_learn_on_success = (
            bool(settings.SEMANTIC_RETRIEVAL_AUTO_LEARN_ON_SUCCESS)
            if auto_learn_on_success is None
            else bool(auto_learn_on_success)
        )

    @staticmethod
    def _serialize_cell(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, time):
            return value.strftime("%H:%M:%S")
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _default_domain_provider() -> Any:
        try:
            from app.domains.registry import DomainRegistry

            return DomainRegistry.get_current_domain()
        except Exception:
            return None

    @staticmethod
    def _fallback_enum_label(column: str, value: int) -> Any:
        fallback_labels = {
            "status": {0: "Pending", 1: "In Progress", 2: "Completed", 3: "Overdue"},
            "facility_status": {0: "Pending", 1: "In Progress", 2: "Completed", 3: "Delay In Progress"},
        }
        return (fallback_labels.get(str(column or "").strip().lower(), {}) or {}).get(value, value)

    @classmethod
    def _serialize_row(cls, row: Dict, domain_provider: Callable[[], Any] | None = None):
        # Defense-in-depth: mask any sensitive column value that slipped through
        # (e.g. via SELECT *) so secrets never reach the LLM or the user.
        serialized = {
            k: ("[redacted]" if SQLValidatorService.is_sensitive_column(k) else cls._serialize_cell(v))
            for k, v in dict(row or {}).items()
        }

        domain = None
        try:
            if callable(domain_provider):
                domain = domain_provider()
            else:
                domain = cls._default_domain_provider()
        except Exception:
            domain = None

        enum_columns = set()
        getter = getattr(domain, "enum_columns", None) if domain is not None else None
        if callable(getter):
            try:
                enum_columns = {str(c or "").strip().lower() for c in getter() if str(c or "").strip()}
            except Exception:
                enum_columns = set()
        enum_columns |= {"status", "facility_status"}

        for key, value in list(serialized.items()):
            column = str(key or "").strip()
            if not column:
                continue
            normalized_column = column.split(".")[-1].strip().lower()
            if normalized_column not in enum_columns:
                continue

            raw_value = value
            if isinstance(raw_value, str) and raw_value.isdigit():
                raw_value = int(raw_value)

            label = raw_value
            if domain is not None:
                try:
                    label = domain.get_enum_label(normalized_column, raw_value)
                except Exception:
                    label = raw_value
            if label == raw_value and isinstance(raw_value, int):
                label = cls._fallback_enum_label(normalized_column, raw_value)
            if label != raw_value:
                serialized[key] = label

        return serialized

    @staticmethod
    def _extract_window_total_count(rows: list[Dict]) -> int | None:
        if not rows:
            return None
        first = dict(rows[0] or {})
        value = first.get("_total_count")
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else None
        except Exception:
            return None

    @staticmethod
    def _strip_window_total_count(rows: list[Dict]) -> list[Dict]:
        cleaned: list[Dict] = []
        for row in rows or []:
            item = dict(row or {})
            item.pop("_total_count", None)
            cleaned.append(item)
        return cleaned

    @staticmethod
    def _latest_user_query(messages: List[Any]) -> str:
        for message in reversed(messages or []):
            message_type = str(getattr(message, "type", "") or "").strip().lower()
            if message_type not in {"human", "user"}:
                continue
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    @staticmethod
    def _candidate_tables_from_state(state: Dict[str, Any]) -> list[str]:
        intent = state.get("intent") if isinstance(state.get("intent"), dict) else {}
        values = [intent.get("table")] + list(intent.get("joins") or [])
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip()
            lowered = cleaned.lower()
            if not cleaned or lowered in seen:
                continue
            seen.add(lowered)
            out.append(cleaned)
        return out

    async def run(self, state: Dict) -> Dict:
        if state.get("error"):
            return {}

        sql = state.get("sql_query")
        if not sql or sql == "SKIP":
            return {}

        metadata = state.get("metadata", {})
        db_url = str(
            metadata.get("db_connection_string")
            or getattr(settings, "DATABASE_URL", "")
            or ""
        ).strip()
        if not db_url:
            raise ValueError(
                "No database connection resolved for this request. Chat routing is "
                "appcode/token driven; the token did not resolve to an app database "
                "and no DATABASE_URL default is configured."
            )

        # Statement timeout is enforced at the connection level (see
        # SchemaService._register_statement_timeout), so the hot path here adds
        # no extra round-trips. The fetch cap below is the memory backstop.
        try:
            fetch_cap = int(getattr(settings, "SQL_MAX_LIMIT", 1000))
        except (TypeError, ValueError):
            fetch_cap = 1000

        nl_query = self._latest_user_query(state.get("messages") or [])
        audit_started_at = perf_counter()
        try:
            engine = self.schema.get_engine_for_url(db_url)
            # Generated SQL is authored in MySQL syntax; convert it to the
            # target dialect (no-op for MySQL) before execution.
            execution_sql = dialect.to_execution_sql(sql, db_url)
            with engine.connect() as conn:
                result = conn.execute(text(execution_sql))
                if result.returns_rows:
                    # Hard fetch cap: a final memory backstop in case a query
                    # reaches here without a LIMIT (cap <= 0 disables it).
                    mapped = result.mappings()
                    fetched = mapped.fetchmany(fetch_cap) if fetch_cap > 0 else mapped.all()
                    rows = [self._serialize_row(dict(row), self.domain_provider) for row in fetched]
                    total_records = self._extract_window_total_count(rows)
                    if total_records is not None:
                        rows = self._strip_window_total_count(rows)
                    count = len(rows)
                else:
                    conn.commit()
                    count = int(result.rowcount or 0)
                    rows = [{"status": "ok", "rows_affected": count}]
                    total_records = None

            self._remember_success(state, sql)

            audit_nl2sql(
                metadata=metadata,
                nl_query=nl_query,
                sql=sql,
                status="success",
                row_count=count,
                duration_ms=round((perf_counter() - audit_started_at) * 1000, 2),
            )

            return {
                "sql_result": json.dumps(rows, default=str),
                "row_count": count,
                "rows_preview": rows[:20],
                "total_records": total_records,
                "error": None,
            }
        except Exception as exc:
            logger.exception(
                "SQL execution failed domain=%s tables=%s db_configured=%s",
                str(metadata.get("domain_name") or "").strip() or "unknown",
                self._candidate_tables_from_state(state),
                bool(str(db_url or "").strip()),
            )
            audit_nl2sql(
                metadata=metadata,
                nl_query=nl_query,
                sql=sql,
                status="error",
                error=str(exc),
                duration_ms=round((perf_counter() - audit_started_at) * 1000, 2),
            )
            return {"error": "Database query failed."}

    def _remember_success(self, state: Dict[str, Any], sql: str) -> None:
        if not self.auto_learn_on_success:
            return
        if not str(sql or "").strip().lower().startswith("select"):
            return
        retriever = getattr(self, "semantic_retriever", None)
        if retriever is None or not hasattr(retriever, "remember_success"):
            return
        question = self._latest_user_query(state.get("messages") or [])
        if not question:
            return
        try:
            retriever.remember_success(
                question=question,
                sql=sql,
                candidate_tables=self._candidate_tables_from_state(state),
            )
        except Exception:
            return
