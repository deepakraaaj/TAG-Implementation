import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import get_settings
from app.core.lifespan import lifespan
from app.core.logging import setup_logging
from app.core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1.router import api_router

# Setup logging
setup_logging()
settings = get_settings()

app = FastAPI(title="TAG Backend", lifespan=lifespan)

app.add_middleware(
    SecurityHeadersMiddleware,
    frame_ancestors=settings.FRAME_ANCESTORS or [],
)
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
    allow_origins=settings.CORS_ORIGINS or [],
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)

app.include_router(api_router)

# Serve the admin dashboard SPA (the /admin API enforces the token; the static
# bundle is just the shell that prompts for it).
_admin_static = os.path.join(os.path.dirname(__file__), "static", "admin")
if os.path.isdir(_admin_static):
    app.mount("/dashboard", StaticFiles(directory=_admin_static, html=True), name="dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "development",
        ssl_certfile=settings.APP_SSL_CERTFILE or None,
        ssl_keyfile=settings.APP_SSL_KEYFILE or None,
    )
