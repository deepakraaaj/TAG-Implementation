"""Metrics endpoint for Prometheus scraping."""
from fastapi import APIRouter, Response

from app.services.metrics_service import MetricsService

router = APIRouter()
metrics_service = MetricsService()


@router.get("/metrics")
async def get_metrics():
    """
    Prometheus metrics endpoint.
    
    Returns metrics in Prometheus text format for scraping.
    """
    metrics_data = metrics_service.get_metrics()
    return Response(
        content=metrics_data,
        media_type=metrics_service.get_content_type()
    )
