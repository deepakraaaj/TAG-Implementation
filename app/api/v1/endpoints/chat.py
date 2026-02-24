from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Annotated, Optional
import json
import logging
import base64
import asyncio
import uuid
import time

from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.user_service import UserService

router = APIRouter()
logger = logging.getLogger(__name__)
chat_service = ChatService()
user_service = UserService()


def _decode_user_context(raw_header: str) -> dict:
    token = str(raw_header or "").strip()
    if not token:
        return {}
    # Accept URL-safe Base64 and missing padding.
    padding = "=" * (-len(token) % 4)
    decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
    data = json.loads(decoded)
    return data if isinstance(data, dict) else {}


def _build_terminal_error_result(session_id: str, message: str, trace_id: str) -> dict:
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
async def start_session():
    return await chat_service.start_session()

@router.post("/query")
@router.post("/chat")
async def query_tag(
    request: ChatRequest,
    req: Request,
    x_user_context: Annotated[Optional[str], Header()] = None,
    x_trace_id: Annotated[Optional[str], Header()] = None,
    x_response_format: Annotated[Optional[str], Header()] = None,
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    If user_name is missing or invalid, attempts to fetch it from DB.
    """
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
            
            # Inject into request
            if "user_id" in context_data:
                request.user_id = context_data["user_id"]
            elif "userId" in context_data:
                request.user_id = context_data["userId"]
            if "user_role" in context_data:
                request.user_role = context_data["user_role"]
            elif "userRole" in context_data:
                request.user_role = context_data["userRole"]
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
                request.metadata["company_id"] = company_id
                
            # Merge into metadata
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
        user_info = user_service.get_user_info(request.user_id)
        request.metadata["_user_lookup_ms"] = round((time.perf_counter() - user_lookup_started_at) * 1000, 2)
        resolved_name = _clean_text((user_info or {}).get("user_name"))
        if resolved_name:
            request.metadata["user_name"] = resolved_name
            logger.info(f"Resolved User Name: {resolved_name}")

    async def safe_stream():
        try:
            async for chunk in chat_service.generate_chat_stream(request):
                yield chunk
        except asyncio.CancelledError:
            logger.info("Client disconnected during chat stream")
            return
        except Exception as exc:
            logger.exception("Unhandled streaming error: %s", exc)
            error_message = "Internal streaming error"
            yield json.dumps({"type": "error", "message": error_message}) + "\n"
            yield json.dumps(_build_terminal_error_result(request.session_id, error_message, trace_id)) + "\n"

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
