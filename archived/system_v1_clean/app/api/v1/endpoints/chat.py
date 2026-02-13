from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Annotated, Optional
import json
import logging
import base64

from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService
from app.services.user_service import UserService

router = APIRouter()
logger = logging.getLogger(__name__)
chat_service = ChatService()
user_service = UserService()

@router.post("/session/start")
async def start_session():
    return await chat_service.start_session()

@router.post("/query")
@router.post("/chat")
async def query_tag(
    request: ChatRequest,
    req: Request,
    x_user_context: Annotated[Optional[str], Header()] = None
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    If user_name is missing, attempts to fetch it from DB.
    """
    # Base64 Context Decoding
    if x_user_context:
        try:
            # Decode Base64
            decoded_bytes = base64.b64decode(x_user_context)
            decoded_str = decoded_bytes.decode("utf-8")
            context_data = json.loads(decoded_str)
            
            # Inject into request
            if "user_id" in context_data:
                request.user_id = context_data["user_id"]
            if "user_role" in context_data:
                request.user_role = context_data["user_role"]
            if "user_name" in context_data:
                request.metadata["user_name"] = context_data["user_name"]
            if "company_name" in context_data:
                request.metadata["company_name"] = context_data["company_name"]
            if "company_id" in context_data:
                request.metadata["company_id"] = context_data["company_id"]
                
            # Merge into metadata
            if request.metadata is None:
                request.metadata = {}
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

    return StreamingResponse(chat_service.generate_chat_stream(request), media_type="application/x-ndjson")
