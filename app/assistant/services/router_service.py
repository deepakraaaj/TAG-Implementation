import json
import logging
import os
import re

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

settings = get_settings()
logger = logging.getLogger(__name__)


class RouterService:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
        )

    @staticmethod
    def fallback(query: str) -> str:
        """Fallback heuristic for routing when LLM fails."""
        q = (query or "").strip().lower()
        
        # Check for SQL/data queries
        if re.search(r"\b(task|tasks|asset|assets|user|users|facility|facilities|select|insert|update|create|add|edit|modify|show|list|count|get|find)\b", q):
            return "SQL"
        return "CHAT"

    async def route(self, query: str) -> str:
        """Route query to appropriate handler."""
        q = query.lower().strip()
        
        # PRIORITY 1: Help/capability queries ALWAYS go to CHAT
        # This must be checked FIRST before any other routing logic
        help_patterns = [
            r"\b(what can you do|what do you do|help|capabilities|features)\b",
            r"\b(how can you help|what are you|tell me about yourself)\b",
            r"\b(what can i ask|what questions|show me examples|list.*questions|possible questions)\b",
        ]
        if any(re.search(pattern, q) for pattern in help_patterns):
            logger.info(f"Routing to CHAT (help query): {query[:50]}")
            return "CHAT"
        
        # PRIORITY 2: Deterministic fast-path for clear SQL queries
        if self.fallback(query) == "SQL":
            return "SQL"

        # PRIORITY 3: LLM-based classification for ambiguous cases
        prompt = f"""
Classify user message as SQL or CHAT.
Return only JSON: {{"route":"SQL|CHAT"}}
User: {query}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_router",
            )
            raw = str(response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw[start : end + 1])
                route = str(parsed.get("route", "")).upper()
                if route in {"SQL", "CHAT"}:
                    return route
        except Exception:
            pass
        return self.fallback(query)
