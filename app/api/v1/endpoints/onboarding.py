from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas.onboarding import SimpleOnboardingRequest, SimpleOnboardingResponse
from app.services.onboarding import SimpleOnboardingService

router = APIRouter(prefix="/onboarding")
logger = logging.getLogger(__name__)


def _resolve_container(req: Request) -> Any:
    return getattr(getattr(req.app, "state", None), "container", None)


@router.post("/simple", response_model=SimpleOnboardingResponse)
async def run_simple_onboarding(
    payload: SimpleOnboardingRequest,
    req: Request,
) -> SimpleOnboardingResponse:
    container = _resolve_container(req)
    schema_service = getattr(container, "schema_service", None)
    if schema_service is None:
        raise HTTPException(status_code=503, detail="Schema service is unavailable")

    service = SimpleOnboardingService(schema_service=schema_service)
    try:
        return service.build(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Simple onboarding failed")
        raise HTTPException(status_code=500, detail="Simple onboarding failed") from exc
