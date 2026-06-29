from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text

from app.db import dialect


class DBService:
    """Synchronous DB gateway used by the optional reporting/audit stack."""

    def __init__(self, db_url: str | None = None) -> None:
        # No default is valid: chat routing is appcode/token driven, so report
        # queries pass a per-request db_url. The default engine is created lazily
        # only when a default URL is configured.
        self.db_url = str(db_url or "").strip()
        self._engine_cache: dict[str, Any] = {}
        self.engine_url = self._normalize_engine_url(self.db_url) if self.db_url else ""
        self.engine = self._get_or_create_engine(self.db_url) if self.db_url else None

    @classmethod
    def _normalize_engine_url(cls, db_url: str) -> str:
        return dialect.sync_engine_url(db_url)

    def _get_or_create_engine(self, db_url: str | None = None):
        normalized_db_url = str(db_url or self.db_url).strip()
        if not normalized_db_url:
            raise RuntimeError(
                "No database URL available for this report query. Chat routing is "
                "appcode/token driven; pass a db_url (resolved from the request "
                "token) or configure a DATABASE_URL default."
            )
        engine_url = self._normalize_engine_url(normalized_db_url)
        engine = self._engine_cache.get(engine_url)
        if engine is None:
            engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
            if dialect.detect_dialect(normalized_db_url) != "sqlite":
                engine_kwargs["connect_args"] = dialect.connect_args(
                    normalized_db_url,
                    {"connect_timeout": 5},
                    driver_url=engine_url,
                )
            engine = create_engine(
                engine_url,
                **engine_kwargs,
            )
            self._engine_cache[engine_url] = engine
        return engine

    @staticmethod
    def _normalize_sql(sql: str, params: Any, dialect_name: str) -> tuple[str, Any]:
        normalized_sql = str(sql or "")
        if dialect_name == "postgresql" and params is None:
            # Fully-rendered report SQL is authored in MySQL syntax; convert it.
            # Parameterised SQL is skipped: sqlglot rewrites placeholders
            # (%s / :name) in ways that break the driver binding.
            normalized_sql = dialect.to_execution_sql(normalized_sql, "postgresql://")
        if params is None:
            return normalized_sql, None
        if dialect_name != "sqlite":
            return normalized_sql, params
        if isinstance(params, (list, tuple)):
            return normalized_sql.replace("%s", "?"), tuple(params)
        return normalized_sql, params

    def execute_query(
        self,
        sql: str,
        params: Any = None,
        db_url: str | None = None,
    ) -> list[dict[str, Any]]:
        engine = self._get_or_create_engine(db_url)
        dialect_name = str(getattr(engine.dialect, "name", "") or "")
        normalized_sql, normalized_params = self._normalize_sql(sql, params, dialect_name)
        with engine.connect() as conn:
            if isinstance(normalized_params, dict):
                result = conn.execute(text(normalized_sql), normalized_params)
            elif normalized_params is None:
                result = conn.execute(text(normalized_sql))
            else:
                result = conn.exec_driver_sql(normalized_sql, normalized_params)
            if not result.returns_rows:
                return []
            return [dict(row) for row in result.mappings().all()]

    def execute_update(
        self,
        sql: str,
        params: Any = None,
        db_url: str | None = None,
    ) -> int:
        engine = self._get_or_create_engine(db_url)
        dialect_name = str(getattr(engine.dialect, "name", "") or "")
        normalized_sql, normalized_params = self._normalize_sql(sql, params, dialect_name)
        with engine.begin() as conn:
            if isinstance(normalized_params, dict):
                result = conn.execute(text(normalized_sql), normalized_params)
            elif normalized_params is None:
                result = conn.execute(text(normalized_sql))
            else:
                result = conn.exec_driver_sql(normalized_sql, normalized_params)
            return int(result.rowcount or 0)

    def ping(self) -> bool:
        if self.engine is None:
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def close(self) -> None:
        for engine in self._engine_cache.values():
            engine.dispose()
        self._engine_cache.clear()
