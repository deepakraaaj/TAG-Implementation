from typing import Dict, Optional
# from langchain_groq import ChatGroq
from ..config import get_settings
import logging
import os
import re
import json

logger = logging.getLogger(__name__)
settings = get_settings()

class RouterNode:
    ROUTER_SYSTEM_PROMPT = """
You are an intent router for a facility assistant.
Classify the user query into exactly one route:
- SQL: asks for structured transactional data or CRUD operations in app DB.
- VECTOR: asks for policies, manuals, process guidance, "how-to" docs.
- CHAT: greetings, small talk, bot capability questions, conversational help.

Decision rules:
1. If user asks to list/show/count/find records or asks for current status of entities, choose SQL.
2. If user asks "how to", SOP/policy/process explanations, choose VECTOR.
3. If user asks about assistant capability, greetings, or general conversation, choose CHAT.
4. For ambiguous operational requests, prefer SQL over VECTOR.
5. Apply rules across all languages, not only English.
6. If the user gives a short follow-up answer to the assistant's previous clarification question
   (for example a name/value like "VAIOT Box"), preserve the original operational intent.
   If that prior intent was create/update/list data, choose SQL.

Examples:
- "list facilities" -> SQL
- "show all users" -> SQL
- "how do I create a facility" -> VECTOR
- "what can you do for me" -> CHAT
- "unnala enna panna mudiyum" -> CHAT

Return ONLY valid JSON:
{"route":"SQL|VECTOR|CHAT"}
No markdown. No extra keys.
"""

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
                if route in {"SQL", "VECTOR", "CHAT"}:
                    return route
        except Exception:
            pass

        # Fallback for imperfect model outputs.
        clean = content.replace("*", "").replace("`", "").upper()
        match = re.search(r"\b(SQL|VECTOR|CHAT)\b", clean)
        if match:
            return match.group(1)

        return None

    async def route_query(self, state: Dict) -> Dict:
        """
        Classifies the user query into SQL, VECTOR, or CHAT.
        """
        logger.info("Entering router_node")
        messages = state["messages"]
        last_message = state.get("rewritten_query") or messages[-1].content
        logger.info(f"Routing Contextualized Query: {last_message}")

        if not str(last_message).strip():
            logger.info("Empty query detected in router; defaulting to CHAT.")
            return {"route": "CHAT"}

        # Include recent turns (user + assistant) so follow-up answers can preserve intent.
        history_tail = []
        for msg in messages[-4:]:
            role = "user" if msg.__class__.__name__.lower().startswith("human") else "assistant"
            history_tail.append(f"{role}: {msg.content}")
        history_context = "\n".join(history_tail)

        prompt = (
            f"{self.ROUTER_SYSTEM_PROMPT}\n\n"
            f"Conversation:\n{history_context}\n\n"
            f"User Query: {last_message}"
        )
        
        try:
            response = await self.llm.ainvoke(prompt, max_tokens=30)
            raw_content = response.content.strip()
            logger.info(f"Router Raw Response: {raw_content}")

            parsed_route = self._extract_route(raw_content)
            route = parsed_route or "CHAT"
            if route == "CHAT" and parsed_route is None:
                logger.warning("Router output was unparsable; defaulting to CHAT.")
        except Exception as e:
            logger.error(f"Router failed: {e}. Defaulting to CHAT.")
            route = "CHAT"
            
        logger.info(f"Routing query '{last_message}' to: {route}")
        return {"route": route}
