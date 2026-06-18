"""
Database dialect helpers.

The application was originally MySQL-only. To support PostgreSQL connections
(e.g. the VTS database) without rewriting every hand-built SQL string, this
module centralises three concerns:

1. URL/driver normalisation -- pick the right sync driver for inspection and
   query execution (``pymysql``/``mysqlconnector`` for MySQL, ``psycopg2`` for
   PostgreSQL) and strip params the driver cannot understand.
2. ``search_path`` handling -- PostgreSQL schemas are selected per-connection.
   A non-standard ``search_path`` query param on the URL is extracted here and
   applied through ``connect_args`` so callers can keep using a plain URL.
3. SQL portability -- generated SQL is authored in MySQL syntax. When the
   active connection is PostgreSQL we transpile it with ``sqlglot`` at the
   execution boundary, which converts identifier quoting, date functions, etc.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import sqlglot

# sqlglot renders MySQL ``:name`` placeholders as psycopg2 ``%(name)s``; SQLAlchemy
# ``text()`` expects ``:name``, so convert them back after transpilation.
_PYFORMAT_NAMED = re.compile(r"%\((\w+)\)s")

logger = logging.getLogger(__name__)

# JDBC-style params that appear in copied MySQL URLs but are rejected by the
# Python sync drivers (mysql-connector / pymysql). ``charset`` is intentionally
# NOT included: mysql-connector accepts it and existing URLs rely on it.
_JDBC_BLOCKED = {"allowPublicKeyRetrieval", "useSSL"}

# Custom (non-driver) query param used to carry the PostgreSQL schema.
_SEARCH_PATH_PARAM = "search_path"


def detect_dialect(db_url: str | None) -> str:
    """Return a coarse dialect name: 'postgresql', 'mysql', 'sqlite' or 'unknown'."""
    url = str(db_url or "").lower()
    if url.startswith("postgres") or "+asyncpg" in url or "+psycopg" in url:
        return "postgresql"
    if url.startswith("mysql") or "aiomysql" in url or "pymysql" in url or "asyncmy" in url:
        return "mysql"
    if "sqlite" in url:
        return "sqlite"
    return "unknown"


def is_postgres(db_url: str | None) -> bool:
    return detect_dialect(db_url) == "postgresql"


def _split_custom_params(db_url: str) -> tuple[str, dict[str, str]]:
    """Return (url_without_blocked_or_custom_params, {custom params})."""
    parsed = urlsplit(db_url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept: list[tuple[str, str]] = []
    custom: dict[str, str] = {}
    for key, value in pairs:
        if key == _SEARCH_PATH_PARAM:
            custom[key] = value
        elif key in _JDBC_BLOCKED:
            continue
        else:
            kept.append((key, value))
    cleaned = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(kept), parsed.fragment)
    )
    return cleaned, custom


def search_path_for(db_url: str | None) -> str | None:
    """Extract the requested PostgreSQL search_path, if any."""
    if not db_url:
        return None
    _cleaned, custom = _split_custom_params(str(db_url))
    value = custom.get(_SEARCH_PATH_PARAM)
    return value or None


def sync_engine_url(db_url: str | None) -> str:
    """
    Normalise a (possibly async) URL to a synchronous SQLAlchemy URL suitable
    for ``create_engine`` for query execution.
    """
    raw = str(db_url or "").strip()
    if not raw:
        return raw
    dialect = detect_dialect(raw)
    cleaned, _custom = _split_custom_params(raw)
    if dialect == "postgresql":
        # Force the sync driver regardless of how the URL was written.
        cleaned = (
            cleaned.replace("postgresql+asyncpg", "postgresql+psycopg2")
            .replace("postgres+asyncpg", "postgresql+psycopg2")
        )
        if cleaned.startswith("postgresql://"):
            cleaned = cleaned.replace("postgresql://", "postgresql+psycopg2://", 1)
        elif cleaned.startswith("postgres://"):
            cleaned = cleaned.replace("postgres://", "postgresql+psycopg2://", 1)
        return cleaned
    if dialect == "mysql":
        return cleaned.replace("mysql+aiomysql", "mysql+mysqlconnector")
    return cleaned


def sync_inspection_url(db_url: str | None) -> str:
    """
    Like :func:`sync_engine_url` but prefers ``pymysql`` for MySQL inspection
    (matches the pre-existing SchemaService behaviour).
    """
    raw = str(db_url or "").strip()
    if not raw:
        return raw
    if detect_dialect(raw) == "mysql":
        cleaned, _custom = _split_custom_params(raw)
        return (
            cleaned.replace("mysql+aiomysql", "mysql+pymysql")
            .replace("mysql+asyncmy", "mysql+pymysql")
            .replace("mysql+mysqlconnector", "mysql+pymysql")
        )
    return sync_engine_url(raw)


def async_engine_url(db_url: str | None) -> str:
    """Normalise a URL to an async SQLAlchemy URL (aiomysql / asyncpg)."""
    raw = str(db_url or "").strip()
    if not raw:
        return raw
    cleaned, _custom = _split_custom_params(raw)
    if detect_dialect(raw) == "postgresql":
        if cleaned.startswith("postgresql+psycopg2"):
            cleaned = cleaned.replace("postgresql+psycopg2", "postgresql+asyncpg")
        elif cleaned.startswith("postgresql://"):
            cleaned = cleaned.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif cleaned.startswith("postgres://"):
            cleaned = cleaned.replace("postgres://", "postgresql+asyncpg://", 1)
    return cleaned


def connect_args(db_url: str | None, base: dict | None = None) -> dict:
    """
    Build ``connect_args`` for ``create_engine``, injecting the PostgreSQL
    ``search_path`` (via psycopg2 ``options``) when present.
    """
    args = dict(base or {})
    schema = search_path_for(db_url)
    if schema and detect_dialect(db_url) == "postgresql":
        # psycopg2 understands libpq options; this sets search_path per session.
        args["options"] = f"-csearch_path={schema}"
    return args


def to_execution_sql(sql: str, db_url: str | None) -> str:
    """
    Convert MySQL-authored SQL to the dialect of the target connection.

    For non-PostgreSQL targets the SQL is returned unchanged. For PostgreSQL the
    statement is transpiled with sqlglot (backticks -> double quotes,
    ``CURDATE()`` -> ``CURRENT_DATE``, ``IFNULL`` -> ``COALESCE`` ...). Named
    ``:param`` bind placeholders are preserved so SQLAlchemy ``text()`` binding
    keeps working. On any parse failure the original SQL is returned so
    behaviour degrades gracefully rather than dropping the query.

    Note: SQL containing positional ``%s`` placeholders must not be passed here
    (sqlglot treats ``%`` as modulo); callers using positional params should
    skip transpilation.
    """
    statement = str(sql or "")
    if not statement.strip() or not is_postgres(db_url):
        return statement
    try:
        # identify=True force-quotes every identifier so MySQL table/column
        # names that are reserved words in PostgreSQL (notably ``user``) are
        # emitted as e.g. "user" rather than failing to parse. The VTS schema
        # is lower-case, so quoting lower-case names is safe.
        converted = sqlglot.transpile(statement, read="mysql", write="postgres", identify=True)
        if converted:
            return _PYFORMAT_NAMED.sub(r":\1", converted[0])
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sqlglot mysql->postgres transpile failed, using original SQL: %s", exc)
    return statement


def sqlglot_write_dialect(db_url: str | None) -> str:
    """sqlglot ``write`` dialect name for the active connection."""
    return "postgres" if is_postgres(db_url) else "mysql"


def quote_identifier(name: str, db_url: str | None) -> str:
    """Quote a SQL identifier for the active dialect."""
    safe = str(name).replace('"', "").replace("`", "")
    if is_postgres(db_url):
        return f'"{safe}"'
    return f"`{safe}`"
