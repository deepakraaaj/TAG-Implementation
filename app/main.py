from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from contextlib import asynccontextmanager
from .config import get_settings
from .workflow.graph import create_graph
from langchain_core.messages import HumanMessage
import logging

# Setup logging
logging.basicConfig(level=get_settings().LOG_LEVEL)
logger = logging.getLogger(__name__)

# Global workflow instance
workflow = None

from .services.cache import cache

@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow
    logger.info("Starting TAG Backend...")
    # Initialize services here (DB, Redis, etc.)
    await cache.connect()
    workflow = create_graph()
    yield
    await cache.close()
    logger.info("Shutting down TAG Backend...")

app = FastAPI(title="TAG Backend", lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from typing import Optional, List, Dict, Any, Literal

class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: Optional[str] = None
    user_role: Optional[str] = "user"
    metadata: Optional[Dict[str, Any]] = {}

class SQLResponse(BaseModel):
    ran: bool = False
    cached: bool = False
    query: Optional[str] = None
    row_count: Optional[int] = None
    rows_preview: Optional[List[Dict[str, Any]]] = None

class ChatResponse(BaseModel):
    session_id: str
    message: str
    status: Literal["ok", "needs_filters", "workflow_active", "cancelled", "error"]
    labels: List[str] = []
    # workflow: Optional[WorkflowResponse] = None # Simpler for now
    sql: Optional[SQLResponse] = None
    toon: Optional[Dict[str, Any]] = None
    provider_used: str = "tag_backend"
    trace_id: str = ""

@app.post("/session/start")
async def start_session():
    import uuid
    return {"session_id": str(uuid.uuid4()), "message": "Session started"}


from fastapi.responses import StreamingResponse
import json

@app.get("/health")
async def health_check():
    return {"status": "ok", "env": get_settings().APP_ENV}

async def generate_chat_stream(request: ChatRequest):
    if not workflow:
        yield json.dumps({"type": "error", "message": "Workflow not initialized"}) + "\n"
        return

    # --- Cache Check ---
    cache_key = cache.generate_key("chat", request.session_id, request.message)
    cached_response = await cache.get(cache_key)
    
    if cached_response:
        logger.info(f"Cache HIT for key: {cache_key}")
        # Simulate streaming for cached response if needed, or just yield it
        # Modify the cached response to show it was cached
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

        # 1. Stream the message content (simulate stream) - skip for simplicity in cache logic implementation
        # But we should keep it for UX.
        # Actually, for cache saves, we need the final result.
        
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

from fastapi import Header
from typing import Annotated, Optional
import base64

@app.post("/query")
@app.post("/chat")
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
@app.post("/debug/index")
async def index_documents(background_tasks: BackgroundTasks):
    """
    Trigger document indexing (Debug only).
    """
    from .services.vector import vector_service
    
    sample_docs = [
        {
            "title": "How to Add a New User",
            "content": "To add a new user to the facility management system: 1. Navigate to the Users section. 2. Click 'Add User' button. 3. Fill in the user details including name, email, role, and department. 4. Assign appropriate permissions. 5. Click 'Save' to create the user account.",
            "metadata": {"category": "user_management", "type": "guide"}
        },
        {
            "title": "Maintenance Request Process",
            "content": "When submitting a maintenance request: 1. Go to the Maintenance section. 2. Click 'New Request'. 3. Select the facility and asset requiring maintenance. 4. Describe the issue in detail. 5. Set priority level (Low, Medium, High, Critical). 6. Attach photos if applicable. 7. Submit the request. The maintenance team will be notified automatically.",
            "metadata": {"category": "maintenance", "type": "process"}
        },
        {
            "title": "Safety Protocols for Chemical Storage",
            "content": "Chemical storage safety guidelines: 1. Store chemicals in designated areas only. 2. Keep incompatible chemicals separated. 3. Ensure proper ventilation in storage areas. 4. Label all containers clearly with contents and hazard warnings. 5. Maintain Material Safety Data Sheets (MSDS) for all chemicals. 6. Use appropriate PPE when handling chemicals. 7. Report any spills or leaks immediately to the safety officer.",
            "metadata": {"category": "safety", "type": "policy"}
        }
    ]
    
    async def run_indexing():
        logger.info("Starting background indexing...")
        for doc in sample_docs:
            try:
                await vector_service.index_document(
                    content=doc['content'],
                    metadata={**doc['metadata'], 'title': doc['title']}
                )
            except Exception as e:
                logger.error(f"Indexing failed for {doc['title']}: {e}")
        logger.info("Background indexing complete.")

    background_tasks.add_task(run_indexing)
    return {"status": "Indexing started", "count": len(sample_docs)}
