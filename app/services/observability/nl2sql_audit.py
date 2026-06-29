"""Structured audit trail for the NL->SQL chat path.

Unlike :class:`AuditService` (which writes report runs to an audit *table*), this
emits the audit record to the application *log*. That is deliberate: chat queries
run against per-tenant **read-only** DB principals, so there is no writable table
to insert into. A structured log line is durable (shipped to the log sink),
tamper-evident at the infra layer, and never depends on DB write access.

One record is emitted per executed (or failed) query, capturing the principal,
tenant/app, the natural-language prompt, the generated SQL, and the outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.config import get_settings

# Dedicated logger so the audit trail can be routed/retained independently.
logger = logging.getLogger("nl2sql.audit")


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def audit_nl2sql(
    *,
    metadata: Optional[Dict[str, Any]],
    nl_query: str,
    sql: str,
    status: str,
    row_count: Optional[int] = None,
    error: Optional[str] = None,
    duration_ms: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> None:
    """Emit one NL->SQL audit record. Never raises (audit must not break chat)."""
    if enabled is None:
        enabled = bool(getattr(get_settings(), "AUDIT_NL2SQL_ENABLED", True))
    if not enabled:
        return

    try:
        meta = metadata if isinstance(metadata, dict) else {}
        record: Dict[str, Any] = {
            "event": "nl2sql_query",
            "status": status,
            "app_id": _coerce(meta.get("app_id")),
            "tenant": _coerce(meta.get("loginFrom") or meta.get("tenant")),
            "domain": _coerce(meta.get("domain_name")),
            "user_id": _coerce(meta.get("user_id") or meta.get("userId")),
            "user_name": _coerce(meta.get("user_name")),
            "company_id": _coerce(meta.get("company_id")),
            "role": _coerce(meta.get("role") or meta.get("user_role")),
            "trace_id": _coerce(meta.get("trace_id")),
            "nl_query": _coerce(nl_query),
            "sql": _coerce(sql),
            "row_count": row_count,
            "duration_ms": duration_ms,
            "error": _coerce(error),
        }
        logger.info("nl2sql_audit", extra={"audit": record})
    except Exception:
        # Auditing is best-effort; never let it interfere with the request.
        logger.exception("Failed to emit NL->SQL audit record")
