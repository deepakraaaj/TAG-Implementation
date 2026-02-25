"""Metrics endpoint for Prometheus scraping."""
from fastapi import APIRouter, Request, Response

from app.core.dependencies import get_container

router = APIRouter()


@router.get("/metrics")
async def get_metrics(req: Request):
    """
    Prometheus metrics endpoint.
    
    Returns metrics in Prometheus text format for scraping.
    """
    container = getattr(req.app.state, "container", None) or get_container()
    metrics_service = container.metrics_service
    metrics_data = metrics_service.get_metrics()
    return Response(
        content=metrics_data,
        media_type=metrics_service.get_content_type()
    )
