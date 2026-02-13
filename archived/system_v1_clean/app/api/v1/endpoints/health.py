from fastapi import APIRouter
from app.config import get_settings

router = APIRouter()

@router.get("/health")
async def health_check():
    return {"status": "ok", "env": get_settings().APP_ENV}
