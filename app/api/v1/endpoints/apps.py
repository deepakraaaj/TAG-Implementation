from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.domains.registry import DomainRegistry

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_container(req: Request) -> Any:
    return getattr(getattr(req.app, "state", None), "container", None)


def _default_company_id(app_config: Any) -> str | None:
    metadata = dict(getattr(app_config, "default_metadata", {}) or {})
    raw_value = metadata.get("company_id")
    value = str(raw_value or "").strip()
    return value or None


def _company_name_column(columns: set[str]) -> str:
    for candidate in ("name", "company_name", "display_name", "title"):
        if candidate in columns:
            return candidate
    return ""


@router.get("/apps")
async def list_apps(req: Request):
    container = _resolve_container(req)
    app_registry = getattr(container, "app_registry", None)
    if app_registry is None or not callable(getattr(app_registry, "enabled", None)) or not app_registry.enabled():
        return {"apps": [], "default_app_id": None}

    apps = []
    for app_id, app_config in app_registry.list_apps():
        apps.append(
            {
                "app_id": app_id,
                "display_name": app_config.display_name,
                "description": app_config.description or "",
                "domain_name": app_config.domain_name or app_id,
                "default_company_id": _default_company_id(app_config),
                "allowed_tables": list(app_config.allowed_tables or []),
                "protected_tables": list(app_config.protected_tables or []),
            }
        )

    return {
        "apps": apps,
        "default_app_id": app_registry.default_app_id,
    }


@router.get("/apps/{app_id}/domain-config")
async def get_app_domain_config(
    app_id: str,
    req: Request,
):
    container = _resolve_container(req)
    app_registry = getattr(container, "app_registry", None)
    if app_registry is None:
        raise HTTPException(status_code=503, detail="Application registry is unavailable")

    try:
        app_config = app_registry.resolve(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    domain_name = app_config.domain_name or app_id
    with DomainRegistry.use_domain(domain_name):
        domain = DomainRegistry.get_current_domain()
        effective_config = domain.get_effective_config_summary()

    return {
        "app_id": app_id,
        "display_name": app_config.display_name,
        "domain_name": domain_name,
        "effective_config": effective_config,
    }


@router.get("/apps/{app_id}/companies")
async def list_app_companies(
    app_id: str,
    req: Request,
    limit: int = Query(default=200, ge=1, le=1000),
):
    container = _resolve_container(req)
    app_registry = getattr(container, "app_registry", None)
    schema_service = getattr(container, "schema_service", None)
    if app_registry is None or schema_service is None:
        raise HTTPException(status_code=503, detail="Application registry is unavailable")

    try:
        app_config = app_registry.resolve(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        try:
            normalized_limit = int(limit)
        except Exception:
            normalized_limit = 200
        normalized_limit = max(1, min(normalized_limit, 1000))

        columns_map = schema_service.get_table_columns(["company"], db_url=app_config.database_url)
        company_columns = {
            str(column or "").strip().lower()
            for column in (columns_map.get("company") or set())
            if str(column or "").strip()
        }
        if "id" not in company_columns:
            return {
                "app_id": app_id,
                "default_company_id": _default_company_id(app_config),
                "companies": [],
            }

        name_column = _company_name_column(company_columns)
        active_column = "is_active" if "is_active" in company_columns else ""
        select_parts = ["company.id AS company_id"]
        if name_column:
            select_parts.append(f"TRIM(COALESCE(company.{name_column}, '')) AS company_name")
        else:
            select_parts.append("CAST(company.id AS CHAR) AS company_name")
        if active_column:
            select_parts.append(f"company.{active_column} AS is_active")

        order_by = "company_name ASC, company_id ASC" if name_column else "company_id ASC"
        sql = text(
            "SELECT "
            + ", ".join(select_parts)
            + " FROM company ORDER BY "
            + order_by
            + f" LIMIT {normalized_limit}"
        )

        engine = schema_service.get_engine_for_url(app_config.database_url)
        with engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load companies for app %s", app_id)
        raise HTTPException(status_code=500, detail=f"Failed to load companies for {app_id}") from exc

    companies = []
    for row in rows:
        company_id = str(row.get("company_id") or "").strip()
        company_name = str(row.get("company_name") or "").strip()
        if not company_id:
            continue
        companies.append(
            {
                "company_id": company_id,
                "company_name": company_name or company_id,
                "is_active": bool(row.get("is_active")) if active_column else None,
            }
        )

    return {
        "app_id": app_id,
        "default_company_id": _default_company_id(app_config),
        "companies": companies,
    }
