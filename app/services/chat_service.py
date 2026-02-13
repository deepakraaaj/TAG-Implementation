import logging
import json
import uuid
from typing import AsyncGenerator, Dict, List

from app.schemas.chat import ChatRequest
from app.services.cache import cache
from app.core import lifespan
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

class ChatService:
    @staticmethod
    def _history_key(session_id: str) -> str:
        return cache.generate_key("history", session_id)

    async def _load_history(self, session_id: str) -> List[Dict[str, str]]:
        history = await cache.get(self._history_key(session_id))
        if isinstance(history, list):
            return [h for h in history if isinstance(h, dict) and "role" in h and "content" in h]
        return []

    async def _save_history(self, session_id: str, history: List[Dict[str, str]]) -> None:
        # Keep recent context bounded for prompt size and cache footprint.
        trimmed = history[-20:]
        await cache.set(self._history_key(session_id), trimmed, ttl=86400)

    async def start_session(self):
        return {"session_id": str(uuid.uuid4()), "message": "Session started"}

    async def generate_chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        workflow = lifespan.workflow
        
        if not workflow:
            yield json.dumps({"type": "error", "message": "Workflow not initialized"}) + "\n"
            return

        history_payload = await self._load_history(request.session_id)

        # --- Cache Check ---
        # Include turn index so repeated phrases in different context don't return stale replies.
        cache_key = cache.generate_key("chat", request.session_id, len(history_payload), request.message)
        cached_response = await cache.get(cache_key)
        
        if cached_response:
            logger.info(f"Cache HIT for key: {cache_key}")
            if cached_response.get("sql"):
                 cached_response["sql"]["cached"] = True

            cached_message = cached_response.get("message")
            if cached_message:
                yield json.dumps({"type": "token", "content": str(cached_message)}) + "\n"
            else:
                yield json.dumps({"type": "token", "content": "I processed your previous request from cache."}) + "\n"

            history_payload.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": str(cached_message or "")},
                ]
            )
            await self._save_history(request.session_id, history_payload)

            yield json.dumps(cached_response, default=str) + "\n"
            return
            
        logger.info(f"Cache MISS for key: {cache_key}")

        try:
            prior_messages = []
            for item in history_payload:
                role = item.get("role")
                content = str(item.get("content", ""))
                if not content:
                    continue
                if role == "assistant":
                    prior_messages.append(AIMessage(content=content))
                else:
                    prior_messages.append(HumanMessage(content=content))

            # Ensure session_id is in metadata for nodes to use
            if not request.metadata:
                request.metadata = {}
            request.metadata["session_id"] = request.session_id

            logger.info(f"Invoking workflow with session_id: {request.session_id}, metadata: {request.metadata}")
            inputs = {
                "messages": prior_messages + [HumanMessage(content=request.message)],
                "metadata": request.metadata,
                "retry_count": 0
            }
            result = await workflow.ainvoke(inputs)
            
            final_message = result["messages"][-1].content or ""
            executed_sql = result.get("sql_query", "")
            error = result.get("error", None)

            yield json.dumps({"type": "token", "content": str(final_message)}) + "\n"

            # 2. Prepare final result
            status_code = "ok"
            if error:
                status_code = "error"
            
            sql_data = None
            if executed_sql and executed_sql != "SKIP":
                sql_data = {
                    "ran": True,
                    "cached": result.get("from_cache", False),
                    "query": executed_sql,
                    "row_count": result.get("row_count"),
                    "rows_preview": result.get("rows_preview")
                }
            
            
            # Extract workflow payload if present
            workflow_payload = result.get("workflow_payload", None)
            
            final_response = {
                "type": "result",
                "session_id": request.session_id,
                "message": str(final_message),
                "status": status_code,
                "labels": [],
                "workflow": workflow_payload,  # Include workflow payload from state
                "sql": sql_data,
                "token_usage": result.get("token_usage", None),
                "provider_used": "tag_backend",
                "trace_id": ""
            }
            
            # --- Cache Save ---
            if status_code == "ok":
                 await cache.set(cache_key, final_response, ttl=3600)

            history_payload.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": str(final_message)},
                ]
            )
            await self._save_history(request.session_id, history_payload)
            
            yield json.dumps(final_response, default=str) + "\n"

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
