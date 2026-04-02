from fastapi import APIRouter
from app.api.v1.endpoints import apps, chat, health, metrics

api_router = APIRouter()
api_router.include_router(apps.router, tags=["apps"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(metrics.router, tags=["metrics"])
