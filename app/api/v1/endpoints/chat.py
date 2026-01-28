from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from typing import Annotated, Optional
import json
import logging
import base64
from langchain_core.messages import HumanMessage

from app.schemas.chat import ChatRequest
from app.services.cache import cache
from app.core import lifespan

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/session/start")
async def start_session():
    import uuid
    return {"session_id": str(uuid.uuid4()), "message": "Session started"}

async def generate_chat_stream(request: ChatRequest):
    workflow = lifespan.workflow
    
    if not workflow:
        yield json.dumps({"type": "error", "message": "Workflow not initialized"}) + "\n"
        return

    # --- Cache Check ---
    cache_key = cache.generate_key("chat", request.session_id, request.message)
    cached_response = await cache.get(cache_key)
    
    if cached_response:
        logger.info(f"Cache HIT for key: {cache_key}")
        if cached_response.get("sql"):
             cached_response["sql"]["cached"] = True
             
        yield json.dumps(cached_response, default=str) + "\n"
        return

    logger.info(f"Cache MISS for key: {cache_key}")

    try:
        inputs = {
            "messages": [HumanMessage(content=request.message)],
            "metadata": request.metadata
        }
        result = await workflow.ainvoke(inputs)
        
        final_message = result["messages"][-1].content or ""
        executed_sql = result.get("sql_query", "")
        toon_data = result.get("toon_data", None)
        error = result.get("error", None)

        yield json.dumps({"type": "token", "content": str(final_message)}) + "\n"

        # 2. Prepare final result
        status_code = "ok"
        if error:
            status_code = "error"
        
        sql_data = None
        if executed_sql:
            sql_data = {
                "ran": True,
                "cached": False,
                "query": executed_sql,
                "row_count": result.get("row_count"),
                "rows_preview": result.get("rows_preview")
            }
        
        final_response = {
            "type": "result",
            "session_id": request.session_id,
            "status": status_code,
            "labels": [],
            "workflow": None,
            "sql": sql_data,
            "toon": toon_data,
            "provider_used": "tag_backend",
            "trace_id": ""
        }
        
        # --- Cache Save ---
        if status_code == "ok":
             await cache.set(cache_key, final_response, ttl=3600)
        
        yield json.dumps(final_response, default=str) + "\n"

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"

@router.post("/query")
@router.post("/chat")
async def query_tag(
    request: ChatRequest,
    x_user_context: Annotated[Optional[str], Header()] = None
):
    """
    Executes the TAG workflow and returns a streaming response (NDJSON).
    Supports 'x-user-context' header (Base64 encoded JSON) to inject user/company ID.
    """
    # Base64 Context Decoding
    if x_user_context:
        try:
            # Decode Base64
            decoded_bytes = base64.b64decode(x_user_context)
            decoded_str = decoded_bytes.decode("utf-8")
            context_data = json.loads(decoded_str)
            
            logger.info(f"Decoded Context: {context_data}")
            
            # Inject into request
            if "user_id" in context_data:
                request.user_id = context_data["user_id"]
            if "user_role" in context_data:
                request.user_role = context_data["user_role"]
                
            # Merge into metadata
            if request.metadata is None:
                request.metadata = {}
            request.metadata.update(context_data)
            
        except Exception as e:
            logger.error(f"Failed to decode x-user-context: {e}")
            # We don't fail the request, just log and ignore invalid context
            
    return StreamingResponse(generate_chat_stream(request), media_type="application/x-ndjson")
