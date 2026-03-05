from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from typing import Annotated, Any, Optional
import json
import logging
import base64
import asyncio
import uuid
import time

from app.core.dependencies import get_container
from app.schemas.chat import ChatRequest

router = APIRouter()
logger = logging.getLogger(__name__)


class _ChatServiceProxy:
    def __init__(self) -> None:
        self._target = None

    def _resolve(self):
        if self._target is None:
            self._target = get_container().chat_service
        return self._target

    async def start_session(self):
        return await self._resolve().start_session()

    async def generate_chat_stream(self, request):
        async for chunk in self._resolve().generate_chat_stream(request):
            yield chunk

    def _build_final_response(self, *args, **kwargs):
        return self._resolve()._build_final_response(*args, **kwargs)


class _UserServiceProxy:
    def __init__(self) -> None:
        self._target = None

    def _resolve(self):
        if self._target is None:
            self._target = get_container().user_service
        return self._target

    def get_user_info(self, user_id):
        return self._resolve().get_user_info(user_id)


# Backward-compatible module globals used by unit tests for monkeypatching.
chat_service: Any = _ChatServiceProxy()
user_service: Any = _UserServiceProxy()


def _resolve_services(req: Optional[Request]) -> tuple[Any, Any]:
    app = getattr(req, "app", None)
    container = getattr(getattr(app, "state", None), "container", None)
    if container is not None:
        return container.chat_service, container.user_service
    return chat_service, user_service


def _decode_user_context(raw_header: str) -> dict:
    token = str(raw_header or "").strip()
    if not token:
        return {}
    # Accept URL-safe Base64 and missing padding.
    padding = "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
    data = json.loads(decoded)
    return data if isinstance(data, dict) else {}


def _build_terminal_error_result(chat_service: Any, session_id: str, message: str, trace_id: str) -> dict:
    return chat_service._build_final_response(
        session_id,
        message,
        status="error",
        workflow_payload=None,
        sql_data=None,
        trace_id=trace_id,
    )


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _company_name_from_metadata(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    company_obj = metadata.get("company")
    company_obj_name = ""
    if isinstance(company_obj, dict):
        company_obj_name = _clean_text(company_obj.get("name"))
    for candidate in (
        metadata.get("company_name"),
        metadata.get("companyName"),
        company_obj_name,
    ):
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned
    return ""


def _has_usable_user_name(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    user_name = _clean_text(metadata.get("user_name"))
    if not user_name:
        return False
    lowered = user_name.casefold()
    if lowered in {"user", "unknown", "na", "n/a", "null", "none"}:
        return False
    company_name = _company_name_from_metadata(metadata)
    if company_name and lowered == company_name.casefold():
        return False
    return True


@router.post("/session/start")
async def start_session(req: Request = None):
    active_chat_service, _ = _resolve_services(req)
    return await active_chat_service.start_session()

@router.post("/query")
@router.post("/chat")
async def query_tag(
    request: ChatRequest,
    req: Request = None,
    x_user_context: Annotated[Optional[str], Header()] = None,
    x_trace_id: Annotated[Optional[str], Header()] = None,
    x_response_format: Annotated[Optional[str], Header()] = None,
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    If user_name is missing or invalid, attempts to fetch it from DB.
    """
    active_chat_service, active_user_service = _resolve_services(req)

    if request.metadata is None:
        request.metadata = {}
    endpoint_started_at = time.perf_counter()
    trace_id = str(x_trace_id or request.metadata.get("trace_id") or uuid.uuid4().hex).strip()
    request.metadata["trace_id"] = trace_id
    if x_response_format is not None and str(x_response_format).strip():
        request.metadata["response_format"] = str(x_response_format).strip().lower()

    # Base64 Context Decoding
    if x_user_context:
        try:
            context_data = _decode_user_context(x_user_context)
            
            # Inject into request (sanitize embedded quotes from widget encoding)
            raw_uid = context_data.get("user_id") or context_data.get("userId")
            if raw_uid is not None:
                request.user_id = str(raw_uid).strip().strip('"').strip("'").strip()
            if "user_role" in context_data:
                request.user_role = str(context_data["user_role"]).strip().strip('"').strip("'").strip()
            elif "userRole" in context_data:
                request.user_role = str(context_data["userRole"]).strip().strip('"').strip("'").strip()
            if "user_name" in context_data:
                request.metadata["user_name"] = context_data["user_name"]
            if "company_name" in context_data:
                request.metadata["company_name"] = context_data["company_name"]
            company_id = (
                context_data.get("company_id")
                or context_data.get("companyId")
                or (context_data.get("company", {}) or {}).get("id")
            )
            if company_id is not None and str(company_id).strip():
                # Sanitize: strip embedded quotes (widget may double-quote values)
                clean_id = str(company_id).strip().strip('"').strip("'").strip()
                if clean_id.isdigit():
                    request.metadata["company_id"] = int(clean_id)
                else:
                    request.metadata["company_id"] = clean_id
                
            # Merge into metadata, but remove keys we've already sanitized
            for _key in ("company_id", "companyId", "user_id", "userId"):
                context_data.pop(_key, None)
            request.metadata.update(context_data)
            
        except Exception as e:
            logger.error(f"Failed to decode x-user-context: {e}")
            # We don't fail the request, just log and ignore invalid context

    resolved_user_id = _clean_text(
        request.user_id
        or request.metadata.get("user_id")
        or request.metadata.get("userId")
    )
    if resolved_user_id:
        request.user_id = resolved_user_id

    # Auto-Fetch User Name when missing or clearly invalid.
    if request.user_id and not _has_usable_user_name(request.metadata):
        request.metadata.pop("user_name", None)
        logger.info(f"User name missing/invalid for {request.user_id}, fetching from DB...")
        user_lookup_started_at = time.perf_counter()
        user_info = active_user_service.get_user_info(request.user_id)
        request.metadata["_user_lookup_ms"] = round((time.perf_counter() - user_lookup_started_at) * 1000, 2)
        resolved_name = _clean_text((user_info or {}).get("user_name"))
        if resolved_name:
            request.metadata["user_name"] = resolved_name
            logger.info(f"Resolved User Name: {resolved_name}")

    async def safe_stream():
        try:
            async for chunk in active_chat_service.generate_chat_stream(request):
                yield chunk
        except asyncio.CancelledError:
            logger.info("Client disconnected during chat stream")
            return
        except Exception as exc:
            logger.exception("Unhandled streaming error: %s", exc)
            error_message = "Internal streaming error"
            yield json.dumps({"type": "error", "message": error_message}) + "\n"
            yield json.dumps(
                _build_terminal_error_result(
                    active_chat_service,
                    request.session_id,
                    error_message,
                    trace_id,
                )
            ) + "\n"

    request.metadata["_endpoint_pre_stream_ms"] = round((time.perf_counter() - endpoint_started_at) * 1000, 2)

    return StreamingResponse(
        safe_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
