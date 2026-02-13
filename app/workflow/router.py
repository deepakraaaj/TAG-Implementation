from typing import Dict, Optional
# from langchain_groq import ChatGroq
from ..config import get_settings
import logging
import os
import re
import json

from app.workflow.engine.executor import WorkflowExecutor
from app.workflow.engine.store import WorkflowSessionStore
from app.services.llm_retry_service import ainvoke_with_retry

logger = logging.getLogger(__name__)
settings = get_settings()

class RouterNode:
    ROUTER_SYSTEM_PROMPT = """
You are an intent router for a facility assistant.
Classify the user query into exactly one route:
- SQL: asks for structured transactional data, listing records, or CRUD operations in app DB (e.g., "list tasks", "show users").
- VECTOR: asks for policies, manuals, process guidance, "how-to" docs.
- CHAT: greetings, small talk, bot capability questions, conversational help.
- WORKFLOW: asks to perform a multi-step guided task (e.g., "create schedule", "maintenance schedule") or to CANCEL/STOP the current task.

Decision rules:
1. If user asks to list/show/count/find records (including tasks), choose SQL.
2. If user asks "how to", SOP/policy/process explanations, choose VECTOR.
3. If user asks about assistant capability, greetings, or general conversation, choose CHAT.
4. If user explicitly asks to "create schedule", "assign task", or "start maintenance schedule", choose WORKFLOW.
5. If user asks to "cancel", "stop", "reset", or "exit" the current operation, choose WORKFLOW.
6. For ambiguous operational requests, prefer SQL over VECTOR.
7. Apply rules across all languages, not only English.
8. If the user gives a short follow-up answer to the assistant's previous clarification question
   (for example a name/value like "VAIOT Box"), preserve the original operational intent.
   If that prior intent was create/update/list data, choose SQL.

Examples:
- "list facilities" -> SQL
- "task list" -> SQL
- "show all users" -> SQL
- "how do I create a facility" -> VECTOR
- "what can you do for me" -> CHAT
- "create maintenance schedule" -> WORKFLOW
- "cancel this" -> WORKFLOW
- "stop" -> WORKFLOW
- "Hello" -> CHAT

Return ONLY valid JSON:
{"route":"SQL|VECTOR|CHAT|WORKFLOW"}
No markdown. No extra keys.
"""
    @staticmethod
    def _deterministic_route(query: str) -> Optional[str]:
        q = (query or "").strip().lower()
        if not q:
            return None

        # Workflow controls / guided flows.
        if re.search(r"\b(cancel|stop|reset|exit)\b", q):
            return "WORKFLOW"
        if re.search(r"\b(create|start|setup|assign)\b.*\b(schedule|workflow)\b", q):
            return "WORKFLOW"

        # Knowledge/documentation questions.
        if re.search(r"\b(how to|how do i|sop|policy|process|manual|guide)\b", q):
            return "VECTOR"

        # Small talk / greeting.
        if re.fullmatch(r"\s*(hi|hello|hey|good morning|good afternoon|good evening)\s*[!.]?\s*", q):
            return "CHAT"

        # Structured data / operational requests should go SQL.
        entity_terms = r"\b(task|tasks|work order|work orders|job|jobs|user|users|asset|assets|facility|facilities|scheduler|schedulers)\b"
        sql_action_terms = (
            r"\b(list|show|count|how many|find|get|pending|completed|in progress|status|priority|last\s+\d+\s+days|last month|next week)\b"
        )
        if re.search(entity_terms, q) and re.search(sql_action_terms, q):
            return "SQL"

        # Short operational follow-ups like "pending tasks".
        if len(q.split()) <= 4 and re.search(entity_terms, q):
            return "SQL"

        return None


    def __init__(self):
        # Use a fast/cheap model for routing
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )
        
        # Initialize executor just to check active sessions efficiently
        # Path to workflow definitions
        # __file__ is app/workflow/router.py, definitions are in app/workflow/definitions
        current_dir = os.path.dirname(os.path.abspath(__file__))
        definitions_dir = os.path.join(current_dir, "definitions")
        self.executor = WorkflowExecutor(definitions_dir)
        logger.info(f"RouterNode initialized. Loaded workflows: {list(self.executor.registry.registry.keys())} from {definitions_dir}")


    @staticmethod
    def _extract_route(raw_content: str) -> Optional[str]:
        content = (raw_content or "").strip()
        if not content:
            return None

        # Prefer strict JSON contract.
        try:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(content[start:end + 1])
                route = str(parsed.get("route", "")).upper()
                if route in {"SQL", "VECTOR", "CHAT", "WORKFLOW"}:
                    return route
        except Exception:
            pass

        # Fallback for imperfect model outputs.
        clean = content.replace("*", "").replace("`", "").upper()
        match = re.search(r"\b(SQL|VECTOR|CHAT|WORKFLOW)\b", clean)
        if match:
            return match.group(1)

        return None

    async def route_query(self, state: Dict) -> Dict:
        """
        Classifies the user query into SQL, VECTOR, CHAT, or WORKFLOW.
        """
        logger.info("Entering router_node")
        messages = state["messages"]
        if messages:
            last_msg = messages[-1]
            last_message_content = getattr(last_msg, "content", str(last_msg))
        else:
            last_message_content = ""
        last_message = state.get("rewritten_query") or last_message_content
        logger.info(f"Routing Contextualized Query: {last_message}")

        if not str(last_message).strip():
            logger.info("Empty query detected in router; defaulting to CHAT.")
            return {"route": "CHAT"}
            
        # 1. Check for explicit universal commands first (Regex)
        normalized = last_message.strip().lower()
        if re.search(r"\b(cancel|stop|reset|exit)\b", normalized):
            logger.info(f"Cancellation command matched in query: {normalized}. Routing to WORKFLOW.")
            return {"route": "WORKFLOW"}

        # 2. Check if there is an active workflow session
        # Optimization: We no longer force WORKFLOW route here. 
        # We let the LLM decide if the user is answering a workflow question or switching context.
        # This allows "Hello" or "list tasks" to work even during a workflow.
        metadata = state.get("metadata", {})
        session_id = metadata.get("session_id", "default_session")
        active_workflow = await self.executor.get_active_workflow(session_id)
        
        # Include recent turns (user + assistant) so follow-up answers can preserve intent.
        history_tail = []
        for msg in messages[-4:]:
            role = "user" if msg.__class__.__name__.lower().startswith("human") else "assistant"
            content = getattr(msg, "content", str(msg))
            history_tail.append(f"{role}: {content}")
        history_context = "\n".join(history_tail)

        prompt = (
            f"{self.ROUTER_SYSTEM_PROMPT}\n\n"
            f"Active Workflow Session: {active_workflow or 'None'}\n"
            f"Conversation:\n{history_context}\n\n"
            f"User Query: {last_message}"
        )
        
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                max_tokens=30,
                attempts=3,
                backoff_seconds=0.35,
                validator=lambda r: self._extract_route(str(getattr(r, "content", "")).strip()) is not None,
                task_name="router_llm",
            )
            raw_content = response.content.strip()
            logger.info(f"Router Raw Response: {raw_content}")

            parsed_route = self._extract_route(raw_content)
            if parsed_route:
                route = parsed_route
            else:
                route = self._deterministic_route(last_message) or "CHAT"
                logger.warning("Router output was unparsable; fallback route=%s", route)
        except Exception as e:
            logger.error(f"Router failed after retries: {e}. Applying deterministic fallback.")
            route = self._deterministic_route(last_message) or "CHAT"
            
        logger.info(f"Routing query '{last_message}' to: {route}")
        return {"route": route}
