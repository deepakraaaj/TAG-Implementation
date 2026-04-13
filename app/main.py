from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.core.lifespan import lifespan
from app.core.logging import setup_logging
from app.core.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.api.v1.router import api_router

# Setup logging
setup_logging()
settings = get_settings()

app = FastAPI(title="TAG Backend", lifespan=lifespan)

app.add_middleware(
    RateLimitMiddleware,
    rate_limit_per_minute=settings.RATE_LIMIT_PER_MINUTE,
    trust_proxy_headers=settings.TRUST_PROXY_HEADERS,
)
app.add_middleware(
    RequestContextMiddleware,
    trust_proxy_headers=settings.TRUST_PROXY_HEADERS,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["*"],
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=settings.APP_ENV == "development",
    )
