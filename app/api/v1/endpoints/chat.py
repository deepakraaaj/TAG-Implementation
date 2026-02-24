from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Annotated, Optional
import json
import logging
import base64
import asyncio
import uuid

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
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    If user_name is missing, attempts to fetch it from DB.
    """
    if request.metadata is None:
        request.metadata = {}
    trace_id = str(x_trace_id or request.metadata.get("trace_id") or uuid.uuid4().hex).strip()
    request.metadata["trace_id"] = trace_id

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
            
    # Auto-Fetch User Name if missing
    if request.user_id and "user_name" not in request.metadata:
        logger.info(f"User name missing for {request.user_id}, fetching from DB...")
        user_info = user_service.get_user_info(request.user_id)
        if user_info:
            request.metadata.update(user_info)
            logger.info(f"Resolved User Name: {user_info.get('user_name')}")

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

    return StreamingResponse(safe_stream(), media_type="application/x-ndjson")
