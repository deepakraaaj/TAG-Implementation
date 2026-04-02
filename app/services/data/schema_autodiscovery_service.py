"""Auto-discovery service that builds a synthetic domain manifest from live DB schema."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Tables that are almost never useful for NL2SQL queries.
_NOISE_TABLES: Set[str] = {
    "flyway_schema_history",
    "schema_version",
    "schema_migrations",
    "ar_internal_metadata",
    "django_migrations",
    "django_content_type",
    "django_admin_log",
    "django_session",
    "alembic_version",
    "knex_migrations",
    "knex_migrations_lock",
    "__EFMigrationsHistory",
}


class SchemaAutoDiscoveryService:
    """Introspects a live MySQL database and builds a manifest-compatible dict."""

    def __init__(self, schema_service: Any) -> None:
        self._schema = schema_service
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_manifest(
        self,
        db_url: str | None = None,
        allowed_tables: List[str] | None = None,
        *,
        exclude_noise: bool = True,
    ) -> Dict[str, Any]:
        """Return a manifest dict with ``tables`` and ``query_templates`` keys.

        Parameters
        ----------
        db_url:
            Database URL to inspect.  ``None`` → default engine.
        allowed_tables:
            Restrict to these tables.  ``None`` → all tables in the DB.
        exclude_noise:
            When *True* (default), skip common migration/framework tables.
        """
        cache_key = str(db_url or "__default__")
        if cache_key in self._cache:
            return self._cache[cache_key]

        all_tables = self._schema.get_all_tables(db_url)
        if not all_tables:
            logger.warning("Auto-discovery found no tables for %s", cache_key)
            return {"tables": {}, "query_templates": {}}

        if allowed_tables:
            allowed_set = set(allowed_tables)
            all_tables = [t for t in all_tables if t in allowed_set]

        if exclude_noise:
            all_tables = [t for t in all_tables if t not in _NOISE_TABLES]

        # Fetch columns for all tables in one pass.
        columns_map = self._schema.get_table_columns(all_tables, db_url)

        tables_manifest: Dict[str, Any] = {}
        for table in sorted(all_tables):
            table_name: str = str(table)
            columns = columns_map.get(table_name, set())
            if not columns:
                continue
            tables_manifest[table_name] = self._build_table_entry(table_name, columns, all_tables)

        # Infer simple joins across tables via FK-naming conventions.
        table_set: Set[str] = {str(t) for t in all_tables}
        self._infer_joins(tables_manifest, table_set)

        manifest: Dict[str, Any] = {
            "tables": tables_manifest,
            "query_templates": {},
        }
        self._cache[cache_key] = manifest
        return manifest

    def invalidate(self, db_url: str | None = None) -> None:
        """Drop cached manifest for the given URL."""
        cache_key = str(db_url or "__default__")
        self._cache.pop(cache_key, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_table_entry(
        table: str,
        columns: Set[str],
        all_tables: List[str],
    ) -> Dict[str, Any]:
        """Build a single table manifest entry from column names."""
        # Determine primary key heuristic.
        pk = "id" if "id" in columns else ""

        # Build important_columns — include all columns (the SQL builder
        # uses this set to decide which columns are "safe" to select).
        important: Dict[str, Dict[str, str]] = {}
        for col in sorted(columns):
            important[col] = {"description": _humanize(col)}

        # Generate human-friendly aliases from the table name.
        aliases = _generate_aliases(table)

        entry: Dict[str, Any] = {
            "description": _humanize(table),
            "important_columns": important,
            "aliases": aliases,
        }
        if pk:
            entry["primary_key"] = pk

        # Auto-detect tenant scope.
        for candidate in ("company_id", "tenant_id", "organization_id", "org_id"):
            if candidate in columns:
                entry["tenant_scope"] = {
                    "column": candidate,
                    "template_var": candidate,
                    "metadata_key": candidate,
                }
                break

        return entry

    @staticmethod
    def _infer_joins(
        tables_manifest: Dict[str, Any],
        table_set: Set[str],
    ) -> None:
        """Add ``joins`` dict to tables where FK-like columns point to other tables."""
        for table, entry in tables_manifest.items():
            columns = set((entry.get("important_columns") or {}).keys())
            joins: Dict[str, str] = {}
            for col in columns:
                if not col.endswith("_id"):
                    continue
                # e.g. "user_id" → "user"
                candidate_table = col[:-3]  # strip "_id"
                if candidate_table in table_set and candidate_table != table:
                    joins[candidate_table] = f"{table}.{col} = {candidate_table}.id"
            if joins:
                entry["joins"] = joins


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _humanize(name: str) -> str:
    """Convert ``snake_case`` to ``Title Case``."""
    return re.sub(r"_+", " ", name).strip().title()


def _generate_aliases(table: str) -> List[str]:
    """Generate search-friendly aliases from a table name."""
    aliases: List[str] = [table]
    # "task_transaction" → "task transaction"
    readable = table.replace("_", " ")
    if readable != table:
        aliases.append(readable)
    # Plural/singular heuristic.
    if table.endswith("s"):
        singular = table[:-1]
        aliases.append(singular)
        aliases.append(singular.replace("_", " "))
    else:
        plural = table + "s"
        aliases.append(plural)
        aliases.append(plural.replace("_", " "))
    # Dedupe while preserving order.
    seen: set[str] = set()
    unique: List[str] = []
    for a in aliases:
        low = a.lower()
        if low not in seen:
            seen.add(low)
            unique.append(a)
    return unique
