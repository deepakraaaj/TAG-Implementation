from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import json
import logging
import time
import uuid
from typing import Deque

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.request_context import reset_request_id, set_request_id

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, trust_proxy_headers: bool = False) -> None:
        super().__init__(app)
        self.trust_proxy_headers = bool(trust_proxy_headers)

    def _client_ip(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded_for = str(request.headers.get("x-forwarded-for") or "").strip()
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
        client = getattr(request, "client", None)
        return str(getattr(client, "host", "") or "unknown")

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(request.headers.get("x-request-id") or uuid.uuid4().hex).strip() or uuid.uuid4().hex
        token = set_request_id(request_id)
        request.state.request_id = request_id
        request.state.client_ip = self._client_ip(request)
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "Request failed method=%s path=%s client_ip=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                request.state.client_ip,
                duration_ms,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started_at) * 1000
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "Request completed method=%s path=%s status_code=%s client_ip=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                request.state.client_ip,
                duration_ms,
            )
            return response
        finally:
            reset_request_id(token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        rate_limit_per_minute: int,
        trust_proxy_headers: bool = False,
    ) -> None:
        super().__init__(app)
        self.rate_limit_per_minute = max(1, int(rate_limit_per_minute or 1))
        self.trust_proxy_headers = bool(trust_proxy_headers)
        self.window_seconds = 60.0
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._exempt_prefixes = (
            "/health",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/redoc",
        )

    def _client_ip(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded_for = str(request.headers.get("x-forwarded-for") or "").strip()
            if forwarded_for:
                return forwarded_for.split(",")[0].strip()
        client = getattr(request, "client", None)
        return str(getattr(client, "host", "") or "unknown")

    @staticmethod
    def _request_body_receive(body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return receive

    async def _restore_request_body(self, request: Request, body: bytes) -> None:
        request._receive = self._request_body_receive(body)  # type: ignore[attr-defined]

    async def _session_identifier(self, request: Request) -> str:
        for header_name in ("x-session-id", "x-chat-session-id"):
            value = str(request.headers.get(header_name) or "").strip()
            if value:
                return value

        if request.method not in {"POST", "PUT", "PATCH"}:
            return ""
        if "application/json" not in str(request.headers.get("content-type") or "").lower():
            return ""

        body = await request.body()
        await self._restore_request_body(request, body)
        if not body:
            return ""

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, dict):
            return ""

        return str(payload.get("session_id") or payload.get("sessionId") or "").strip()

    async def _rate_limit_key(self, request: Request) -> str:
        session_id = await self._session_identifier(request)
        if session_id:
            return f"session:{session_id}"
        return f"ip:{self._client_ip(request)}"

    async def _allow(self, key: str) -> tuple[bool, int, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.rate_limit_per_minute:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return False, retry_after, 0

            bucket.append(now)
            remaining = max(0, self.rate_limit_per_minute - len(bucket))
            return True, 0, remaining

    async def dispatch(self, request: Request, call_next) -> Response:
        if any(request.url.path.startswith(prefix) for prefix in self._exempt_prefixes):
            return await call_next(request)

        key = await self._rate_limit_key(request)
        allowed, retry_after, remaining = await self._allow(key)
        if not allowed:
            logger.warning("Rate limit exceeded path=%s key=%s", request.url.path, key)
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please retry shortly.",
                    "retry_after_seconds": retry_after,
                },
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["X-RateLimit-Limit"] = str(self.rate_limit_per_minute)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
