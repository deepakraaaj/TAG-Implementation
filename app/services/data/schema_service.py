from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, event, inspect, text

from typing import Any, Dict, List, Set

from app.config import get_settings
from app.db import dialect

logger = logging.getLogger(__name__)

class SchemaService:
    def __init__(self, db_url: str | None = None):
        self.settings = get_settings()
        self.default_db_url = db_url or None
        self._engine_cache: Dict[str, Any] = {}
        self.schema_cache: Dict[str, str] = {}
        # Column metadata is static at runtime; cache it per (inspection_url, table)
        # so the hot validate path never re-hits information_schema. Keyed by the
        # normalized inspection URL to stay correct across tenants/databases.
        self._column_types_cache: Dict[str, Dict[str, Dict[str, str]]] = {}

        # Initialize default engine only when a default DB is configured. Chat
        # routing is appcode/token driven, so engines are normally created
        # per-request from the resolved app DB URL.
        if self.default_db_url:
            self._get_or_create_engine(self.default_db_url)

    @staticmethod
    def _safe_db_target(db_url: str) -> str:
        try:
            parsed = urlsplit(str(db_url or ""))
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            database = parsed.path.lstrip("/")
            base = f"{parsed.scheme}://{host}{port}".rstrip("/")
            if database:
                return f"{base}/{database}" if base else database
            return base or "<unknown-db>"
        except Exception:
            return "<unknown-db>"

    def _get_or_create_engine(self, db_url: str):
        """
        Get cached engine or create a new one for the given URL.
        Handles dialect adjustments (e.g. aiomysql -> mysqlconnector for sync inspection).
        """
        # Normalize URL for inspection/execution to a synchronous driver
        # (pymysql for MySQL, psycopg2 for PostgreSQL) and strip params the
        # driver cannot understand. The PostgreSQL search_path (if carried on
        # the URL) is applied through connect_args below.
        inspection_url = dialect.sync_inspection_url(db_url)

        safe_target = self._safe_db_target(inspection_url)
        if inspection_url in self._engine_cache:
            logger.debug("Reusing cached DB engine for %s", safe_target)
            return self._engine_cache[inspection_url]

        try:
            logger.info("Creating DB engine for %s", safe_target)
            engine_kwargs: Dict[str, Any] = {
                "pool_pre_ping": True,
            }
            if "sqlite" not in inspection_url.lower():
                engine_kwargs.update(
                    pool_size=self.settings.DB_POOL_SIZE,
                    max_overflow=self.settings.DB_MAX_OVERFLOW,
                    pool_timeout=self.settings.DB_POOL_TIMEOUT,
                    pool_recycle=self.settings.DB_POOL_RECYCLE,
                    connect_args=dialect.connect_args(db_url, {"connect_timeout": 5}, driver_url=inspection_url),
                )
            engine = create_engine(inspection_url, **engine_kwargs)
            self._register_statement_timeout(engine, db_url)
            self._engine_cache[inspection_url] = engine
            logger.info("DB engine ready for %s", safe_target)
            return engine
        except Exception:
            logger.exception("Failed to create engine for %s", safe_target)
            raise

    def _register_statement_timeout(self, engine: Any, db_url: str) -> None:
        """Enforce a per-statement timeout at the connection level so a runaway
        query cannot pin the DB -- set once when each pooled connection is
        established, adding zero round-trips to the per-query hot path."""
        try:
            timeout_ms = int(getattr(self.settings, "SQL_STATEMENT_TIMEOUT_MS", 30000))
        except (TypeError, ValueError):
            timeout_ms = 30000
        if timeout_ms <= 0:
            return

        d = dialect.detect_dialect(db_url)
        if d == "postgresql":
            stmt = f"SET statement_timeout = {timeout_ms}"
        elif d == "mysql":
            stmt = f"SET SESSION max_execution_time = {timeout_ms}"
        else:
            return

        @event.listens_for(engine, "connect")
        def _set_timeout(dbapi_conn, _record):  # pragma: no cover - driver glue
            try:
                cursor = dbapi_conn.cursor()
                cursor.execute(stmt)
                cursor.close()
            except Exception:
                logger.debug("Could not set statement timeout", exc_info=True)

    @staticmethod
    def _sanitize_mysqlconnector_url(db_url: str) -> str:
        """
        Drop JDBC-style params that mysql-connector/python does not accept.
        These params are common in copied MySQL URLs and break SQLAlchemy engine init.
        """
        blocked = {"allowPublicKeyRetrieval", "useSSL"}
        parsed = urlsplit(db_url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_pairs = [(k, v) for (k, v) in query_pairs if k not in blocked]
        if len(filtered_pairs) == len(query_pairs):
            return db_url
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered_pairs), parsed.fragment))

    @staticmethod
    def _primary_key_columns(inspector: Any, table: str) -> List[str]:
        try:
            pk = inspector.get_pk_constraint(table)
        except Exception:
            return []
        columns = pk.get("constrained_columns", []) if isinstance(pk, dict) else []
        return [str(column) for column in columns if column]

    @property
    def engine(self):
        """Backwards compatibility for default engine access."""
        return self._get_or_create_engine(self.default_db_url)
    
    @property
    def inspector(self):
        """Backwards compatibility for default inspector."""
        return inspect(self.engine)
        
    def get_engine_for_url(self, db_url: str | None = None):
        """Public accessor for dynamic engine."""
        url = db_url or self.default_db_url
        return self._get_or_create_engine(url)

    def get_schema_hints(self, db_url: str | None = None) -> str:
        """
        Fetches semantic hints from `ai_schema_note`.
        """
        try:
            engine = self.get_engine_for_url(db_url)
            with engine.connect() as conn:
                # We assume 'answer' contains the description or relevant context for 'table_name'
                # Or we can construct a context: "For questions like '{question}', use table '{table_name}'"
                result = conn.execute(text("SELECT table_name, question, answer FROM ai_schema_note WHERE table_name IS NOT NULL LIMIT 50"))
                rows = result.fetchall()
                
                if not rows:
                    return ""
                
                hints = ["Semantic Hints (Use these to find relevant tables):"]
                for row in rows:
                    hints.append(f"- To answer '{row.question}', check table '{row.table_name}' (Context: {row.answer})")
                
                return "\n".join(hints)
        except Exception:
            logger.debug("Schema hints unavailable for %s", self._safe_db_target(db_url or self.default_db_url))
            return ""

    def get_schema(
        self,
        table_names: List[str] | None = None,
        db_url: str | None = None,
        concise: bool = False,
    ) -> str:
        """
        Returns a string representation of the schema.
        If concise=True, returns a compressed format for token optimization.
        """
        try:
            # Check cache first
            cache_key = f"{db_url or '__default__'}_{'concise' if concise else 'full'}_" + (
                ",".join(sorted(table_names)) if table_names else "all"
            )
            if not db_url and cache_key in self.schema_cache:
                return self.schema_cache[cache_key]

            engine = self.get_engine_for_url(db_url)
            with engine.connect() as conn:
                inspector = inspect(conn)
                if not table_names:
                    try:
                        table_names = inspector.get_table_names()
                    except Exception:
                        logger.exception("Error fetching table names")
                        return ""

                schema_text = []
                for table in table_names:
                    try:
                        columns = inspector.get_columns(table)
                        if concise:
                            # Concise Format: table_name(col1:type, col2:type)
                            # Minimize types: VARCHAR -> STR, INTEGER -> INT
                            col_strings = []
                            pk_cols = self._primary_key_columns(inspector, table)

                            for col in columns:
                                type_str = str(col["type"]).upper()
                                # Simplify types
                                if "VARCHAR" in type_str or "TEXT" in type_str:
                                    type_str = "STR"
                                elif "INT" in type_str:
                                    type_str = "INT"
                                elif "BOOL" in type_str or "BIT" in type_str:
                                    type_str = "BOOL"
                                elif "DATETIME" in type_str:
                                    type_str = "DATETIME"

                                # Mark PK
                                if col["name"] in pk_cols:
                                    type_str += ",PK"

                                col_strings.append(f"{col['name']}:{type_str}")

                            schema_text.append(f"{table}({', '.join(col_strings)})")
                        else:
                            # Verbose Format
                            col_strings = [f"{col['name']} ({col['type']})" for col in columns]
                            pk_cols = self._primary_key_columns(inspector, table)
                            if pk_cols:
                                col_strings = [c + " (PK)" if c.split(" ")[0] in pk_cols else c for c in col_strings]
                            schema_text.append(f"Table: {table}\nColumns: {', '.join(col_strings)}\n")

                    except Exception:
                        logger.warning("Failed to inspect table %s", table, exc_info=True)

                final_schema = "\n".join(schema_text)

                if not db_url:
                    self.schema_cache[cache_key] = final_schema
                return final_schema

        except Exception:
            logger.exception("Schema generation failed")
            return ""

    def get_table_columns(self, table_names: List[str], db_url: str | None = None) -> Dict[str, Set[str]]:
        """Return a mapping of table -> set(column_names) for the given tables.

        Derived from the cached column-type metadata so it shares a single
        information_schema lookup with get_table_column_types instead of issuing
        its own.
        """
        types_map = self.get_table_column_types(table_names, db_url=db_url)
        return {table: set(types.keys()) for table, types in types_map.items()}

    def get_table_column_types(
        self,
        table_names: List[str],
        db_url: str | None = None,
    ) -> Dict[str, Dict[str, str]]:
        """Return mapping of table -> {column_name: normalized_sql_type}.

        Cached per (inspection_url, table): each table is inspected at most once
        per process, so the hot validate path does no DB round-trips on cache hits.
        """
        types_map: Dict[str, Dict[str, str]] = {}
        if not table_names:
            return types_map

        engine = self.get_engine_for_url(db_url)
        cache_key = dialect.sync_inspection_url(db_url or self.default_db_url)
        table_cache = self._column_types_cache.setdefault(cache_key, {})

        missing = [t for t in table_names if t not in table_cache]
        if missing:
            try:
                inspector = inspect(engine)
                for table in missing:
                    try:
                        cols = inspector.get_columns(table)
                        table_types: Dict[str, str] = {}
                        for col in cols:
                            name = str(col.get("name") or "").strip()
                            if not name:
                                continue
                            table_types[name] = str(col.get("type") or "").upper()
                        table_cache[table] = table_types
                    except Exception:
                        logger.warning("Failed to inspect column types for table %s", table, exc_info=True)
            except Exception:
                logger.exception("Failed to inspect table column types")

        for table in table_names:
            if table in table_cache:
                types_map[table] = table_cache[table]
        return types_map

    def invalidate_column_cache(self, db_url: str | None = None) -> None:
        """Drop cached column metadata (call after a schema migration)."""
        if db_url is None:
            self._column_types_cache.clear()
        else:
            self._column_types_cache.pop(dialect.sync_inspection_url(db_url), None)

    def get_all_tables(self, db_url: str | None = None) -> List[str]:
        engine = self.get_engine_for_url(db_url)
        try:
            # Create a fresh inspector for the engine
            inspector = inspect(engine)
            return inspector.get_table_names()
        except Exception:
            logger.exception("Failed to list tables")
            return []

    def ping(self, db_url: str | None = None) -> bool:
        target_url = db_url or self.default_db_url
        try:
            engine = self.get_engine_for_url(target_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("Database ping failed for %s", self._safe_db_target(target_url))
            return False

    def close(self) -> None:
        for engine in self._engine_cache.values():
            try:
                engine.dispose()
            except Exception:
                logger.warning("Failed to dispose DB engine", exc_info=True)
        self._engine_cache.clear()
        self.schema_cache.clear()
