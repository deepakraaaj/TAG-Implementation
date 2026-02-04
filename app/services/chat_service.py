import logging
import json
import uuid
from typing import AsyncGenerator

from app.schemas.chat import ChatRequest
from app.services.cache import cache
from app.core import lifespan
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

class ChatService:
    async def start_session(self):
        return {"session_id": str(uuid.uuid4()), "message": "Session started"}

    async def generate_chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
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
                "metadata": request.metadata,
                "retry_count": 0
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
                    "cached": result.get("from_cache", False),
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
                "token_usage": result.get("token_usage", None),
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
