from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, text

from app.config import get_settings


class DBService:
    """Synchronous DB gateway used by the optional reporting/audit stack."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = str(db_url or get_settings().DATABASE_URL).strip()
        self.engine_url = self._normalize_engine_url(self.db_url)
        self.engine = create_engine(self.engine_url, pool_pre_ping=True)

    @staticmethod
    def _sanitize_mysqlconnector_url(db_url: str) -> str:
        blocked = {"allowPublicKeyRetrieval", "useSSL"}
        parsed = urlsplit(db_url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [(k, v) for (k, v) in query_pairs if k not in blocked]
        if len(filtered_pairs) == len(query_pairs):
            return db_url
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered_pairs), parsed.fragment))

    @classmethod
    def _normalize_engine_url(cls, db_url: str) -> str:
        normalized = str(db_url or "").strip()
        if "aiomysql" in normalized:
            normalized = normalized.replace("mysql+aiomysql", "mysql+mysqlconnector")
            normalized = cls._sanitize_mysqlconnector_url(normalized)
        return normalized

    @staticmethod
    def _normalize_sql(sql: str, params: Any, dialect_name: str) -> tuple[str, Any]:
        normalized_sql = str(sql or "")
        if params is None:
            return normalized_sql, None
        if dialect_name != "sqlite":
            return normalized_sql, params
        if isinstance(params, (list, tuple)):
            return normalized_sql.replace("%s", "?"), tuple(params)
        return normalized_sql, params

    def execute_query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        dialect_name = str(getattr(self.engine.dialect, "name", "") or "")
        normalized_sql, normalized_params = self._normalize_sql(sql, params, dialect_name)
        with self.engine.connect() as conn:
            if isinstance(normalized_params, dict):
                result = conn.execute(text(normalized_sql), normalized_params)
            elif normalized_params is None:
                result = conn.execute(text(normalized_sql))
            else:
                result = conn.exec_driver_sql(normalized_sql, normalized_params)
            if not result.returns_rows:
                return []
            return [dict(row) for row in result.mappings().all()]

    def execute_update(self, sql: str, params: Any = None) -> int:
        dialect_name = str(getattr(self.engine.dialect, "name", "") or "")
        normalized_sql, normalized_params = self._normalize_sql(sql, params, dialect_name)
        with self.engine.begin() as conn:
            if isinstance(normalized_params, dict):
                result = conn.execute(text(normalized_sql), normalized_params)
            elif normalized_params is None:
                result = conn.execute(text(normalized_sql))
            else:
                result = conn.exec_driver_sql(normalized_sql, normalized_params)
            return int(result.rowcount or 0)

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()
