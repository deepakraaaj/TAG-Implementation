from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, inspect, text

from typing import Any, Dict, List, Set

from app.config import get_settings

logger = logging.getLogger(__name__)

class SchemaService:
    def __init__(self, db_url: str | None = None):
        self.settings = get_settings()
        self.default_db_url = db_url or self.settings.DATABASE_URL
        self._engine_cache: Dict[str, Any] = {}
        self.schema_cache: Dict[str, str] = {}

        # Initialize default engine
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
        # Normalize URL for inspection if needed (sync driver)
        inspection_url = db_url
        if "aiomysql" in inspection_url or "asyncmy" in inspection_url:
            inspection_url = inspection_url.replace("mysql+aiomysql", "mysql+pymysql").replace("mysql+asyncmy", "mysql+pymysql")
            inspection_url = self._sanitize_mysqlconnector_url(inspection_url)
        elif "mysqlconnector" in inspection_url:
            inspection_url = inspection_url.replace("mysql+mysqlconnector", "mysql+pymysql")
            inspection_url = self._sanitize_mysqlconnector_url(inspection_url)

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
                    connect_args={"connect_timeout": 5},
                )
            engine = create_engine(inspection_url, **engine_kwargs)
            self._engine_cache[inspection_url] = engine
            logger.info("DB engine ready for %s", safe_target)
            return engine
        except Exception:
            logger.exception("Failed to create engine for %s", safe_target)
            raise

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
        """Return a mapping of table -> set(column_names) for the given tables."""
        columns_map: Dict[str, Set[str]] = {}
        if not table_names:
            return columns_map

        engine = self.get_engine_for_url(db_url)
        try:
            inspector = inspect(engine)
            for table in table_names:
                try:
                    cols = inspector.get_columns(table)
                    columns_map[table] = {col["name"] for col in cols if col.get("name")}
                except Exception:
                    logger.warning("Failed to inspect columns for table %s", table, exc_info=True)
            return columns_map
        except Exception:
            logger.exception("Failed to inspect table columns")
            return columns_map

    def get_table_column_types(
        self,
        table_names: List[str],
        db_url: str | None = None,
    ) -> Dict[str, Dict[str, str]]:
        """Return mapping of table -> {column_name: normalized_sql_type}."""
        types_map: Dict[str, Dict[str, str]] = {}
        if not table_names:
            return types_map

        engine = self.get_engine_for_url(db_url)
        try:
            inspector = inspect(engine)
            for table in table_names:
                try:
                    cols = inspector.get_columns(table)
                    table_types: Dict[str, str] = {}
                    for col in cols:
                        name = str(col.get("name") or "").strip()
                        if not name:
                            continue
                        col_type = str(col.get("type") or "").upper()
                        table_types[name] = col_type
                    types_map[table] = table_types
                except Exception:
                    logger.warning("Failed to inspect column types for table %s", table, exc_info=True)
            return types_map
        except Exception:
            logger.exception("Failed to inspect table column types")
            return types_map

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
