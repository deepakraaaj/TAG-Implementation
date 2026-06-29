from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict

from sqlalchemy import inspect, text

from app.db import dialect
from app.services.interfaces import SchemaGateway

logger = logging.getLogger(__name__)


class UserService:
    def __init__(
        self,
        schema_service: SchemaGateway,
        domain_provider: Callable[[], Any],
    ):
        self.schema_service = schema_service
        self.domain_provider = domain_provider

    @staticmethod
    def _safe_identifier(name: str, default: str) -> str:
        candidate = str(name or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            return candidate
        return default

    def _resolve_lookup_config(self) -> Dict[str, Any]:
        domain = self.domain_provider()
        getter = getattr(domain, "get_user_lookup_config", None)
        if not callable(getter):
            return {}
        config = getter()
        return dict(config or {}) if isinstance(config, dict) else {}

    @staticmethod
    def _normalize_user_id(user_id: Any) -> str:
        return str(user_id or "").strip()

    def get_company_name(self, company_id: Any, db_url: str | None = None) -> str:
        """Authoritative company name from the DB (company table) by id.

        Returns "" when unavailable, so callers can fall back to whatever the
        client supplied.
        """
        normalized = str(company_id or "").strip()
        if not normalized.isdigit():
            return ""
        try:
            engine = self.schema_service.get_engine_for_url(db_url)
            if engine is None:
                return ""
            inspector = inspect(engine)
            if not inspector.has_table("company"):
                return ""
            columns = {
                str(col.get("name")).strip().lower()
                for col in inspector.get_columns("company")
            }
            name_column = next(
                (c for c in ("name", "company_name", "display_name", "title") if c in columns),
                "",
            )
            if not name_column:
                return ""
            q = lambda ident: dialect.quote_identifier(ident, db_url)
            with engine.connect() as conn:
                row = conn.execute(
                    text(f"SELECT {q(name_column)} AS name FROM {q('company')} WHERE {q('id')} = :cid LIMIT 1"),
                    {"cid": int(normalized)},
                ).mappings().first()
                if row:
                    return str(row.get("name") or "").strip()
        except Exception:
            logger.exception("Error fetching company name for company_id=%s", company_id)
        return ""

    def get_user_info(self, user_id: str, db_url: str | None = None) -> Dict[str, str]:
        """
        Fetches user details (name) from the database using user_id.
        """
        try:
            normalized_user_id = self._normalize_user_id(user_id)
            if not normalized_user_id.isdigit():
                return {}

            engine = self.schema_service.get_engine_for_url(db_url)
            if engine is None:
                logger.warning("User lookup skipped because schema engine is unavailable")
                return {}

            lookup_cfg = self._resolve_lookup_config()
            table = self._safe_identifier(lookup_cfg.get("table"), "user")
            id_column = self._safe_identifier(lookup_cfg.get("id_column"), "id")
            first_name_column = self._safe_identifier(lookup_cfg.get("first_name_column"), "first_name")
            last_name_column = self._safe_identifier(lookup_cfg.get("last_name_column"), "last_name")
            fallback_name = str(lookup_cfg.get("fallback_name", "User")).strip() or "User"

            q = lambda ident: dialect.quote_identifier(ident, db_url)
            with engine.connect() as conn:
                stmt = text(
                    "SELECT "
                    f"{q(first_name_column)} AS first_name, "
                    f"{q(last_name_column)} AS last_name "
                    f"FROM {q(table)} WHERE {q(id_column)} = :uid LIMIT 1"
                )
                row = conn.execute(stmt, {"uid": int(normalized_user_id)}).mappings().first()
                if row:
                    first_name = str(row.get("first_name") or "").strip()
                    last_name = str(row.get("last_name") or "").strip()
                    full_name = " ".join(part for part in (first_name, last_name) if part) or fallback_name
                    return {"user_name": full_name}
        except Exception:
            logger.exception("Error fetching user info for user_id=%s", user_id)

        return {}
