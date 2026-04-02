from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings

router = APIRouter()


def _build_check(status_value: str, required: bool, detail: str) -> dict[str, Any]:
    return {
        "status": status_value,
        "required": required,
        "detail": detail,
    }


def _resolve_container(req: Optional[Any]) -> Any:
    if req is None:
        return None
    app = getattr(req, "app", None)
    state = getattr(app, "state", None)
    return getattr(state, "container", None)


async def _build_readiness_payload(req: Optional[Any]) -> dict[str, Any]:
    settings = get_settings()
    container = _resolve_container(req)
    checks: dict[str, dict[str, Any]] = {}

    if container is None:
        checks["container"] = _build_check("not_ready", True, "Service container is not initialized")
        checks["workflow"] = _build_check("not_ready", True, "Workflow graph is not initialized")
        checks["cache"] = _build_check("unknown", False, "Cache state is unavailable before startup")
        return {
            "status": "not_ready",
            "ready": False,
            "env": settings.APP_ENV,
            "checks": checks,
        }

    readiness_snapshot = getattr(container, "readiness_snapshot", None)
    if callable(readiness_snapshot):
        try:
            payload = await readiness_snapshot()
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            checks["container"] = _build_check("ok", True, "Service container is initialized")
            checks["readiness"] = _build_check(
                "not_ready",
                True,
                f"Readiness evaluation failed: {type(exc).__name__}",
            )
            return {
                "status": "not_ready",
                "ready": False,
                "env": settings.APP_ENV,
                "checks": checks,
            }

    checks["container"] = _build_check("ok", True, "Service container is initialized")

    workflow = container.get_workflow()
    if workflow is None:
        checks["workflow"] = _build_check("not_ready", True, "Workflow graph is not initialized")
    else:
        checks["workflow"] = _build_check("ok", True, "Workflow graph is ready")

    cache_backend = getattr(container, "cache", None)
    cache_configured = bool(cache_backend and callable(getattr(cache_backend, "is_configured", None)) and cache_backend.is_configured())
    if not cache_configured:
        checks["cache"] = _build_check("disabled", False, "Redis cache is not configured")
    else:
        cache_ok = False
        ping_cache = getattr(cache_backend, "ping", None)
        if callable(ping_cache):
            try:
                cache_ok = bool(await ping_cache())
            except Exception:
                cache_ok = False
        using_fallback = bool(
            cache_backend
            and callable(getattr(cache_backend, "using_fallback", None))
            and cache_backend.using_fallback()
        )
        if using_fallback:
            checks["cache"] = _build_check(
                "degraded",
                False,
                "Redis cache is unavailable; using in-memory fallback in this process",
            )
        elif cache_ok:
            checks["cache"] = _build_check("ok", False, "Redis cache is reachable")
        else:
            checks["cache"] = _build_check("degraded", False, "Redis cache is unavailable; requests will continue without cache")

    required_failures = any(check["required"] and check["status"] != "ok" for check in checks.values())
    degraded = any(not check["required"] and check["status"] == "degraded" for check in checks.values())
    overall_status = "not_ready" if required_failures else ("degraded" if degraded else "ok")

    return {
        "status": overall_status,
        "ready": not required_failures,
        "env": settings.APP_ENV,
        "checks": checks,
    }


@router.get("/health")
async def health_check(req: Request):
    payload = await _build_readiness_payload(req)
    return JSONResponse(status_code=status.HTTP_200_OK, content=payload)


@router.get("/health/live")
async def liveness_check():
    return {"status": "ok", "alive": True, "env": get_settings().APP_ENV}


@router.get("/health/ready")
async def readiness_check(req: Request):
    payload = await _build_readiness_payload(req)
    http_status = status.HTTP_200_OK if payload["ready"] else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=http_status, content=payload)
