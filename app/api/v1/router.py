from fastapi import APIRouter
from app.api.v1.endpoints import admin, apps, chat, health, metrics, onboarding, semantic

api_router = APIRouter()
api_router.include_router(admin.router, tags=["admin"])
api_router.include_router(apps.router, tags=["apps"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(onboarding.router, tags=["onboarding"])
api_router.include_router(semantic.router, tags=["semantic"])
