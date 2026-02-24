import json
import logging
import os
import re
from typing import Dict, Set, Tuple

from langchain_openai import ChatOpenAI

from app.assistant.services.manifest_catalog import ManifestCatalog
from app.config import get_settings
from app.domains.registry import DomainRegistry
from app.services.llm_retry_service import ainvoke_with_retry
from app.services.token_usage_service import TokenUsageService

settings = get_settings()
logger = logging.getLogger(__name__)


class RouterService:
    _cached_sql_terms: Set[str] | None = None

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

    @classmethod
    def _sql_terms(cls) -> Set[str]:
        if cls._cached_sql_terms is not None:
            return cls._cached_sql_terms

        # Keep core operation verbs generic and provider/domain agnostic.
        terms: Set[str] = {
            "select",
            "insert",
            "update",
            "create",
            "add",
            "edit",
            "modify",
            "show",
            "list",
            "count",
            "get",
            "find",
            "delete",
            "remove",
        }

        try:
            catalog = ManifestCatalog()
            for table_name in catalog.table_names():
                terms.add(str(table_name or "").strip().lower())
                for alias in catalog.aliases(table_name):
                    a = str(alias or "").strip().lower()
                    if a:
                        terms.add(a)
        except Exception:
            pass

        try:
            domain = DomainRegistry.get_current_domain()
            capabilities = domain.get_capabilities() if hasattr(domain, "get_capabilities") else {}
            tables_description = capabilities.get("tables_description") if isinstance(capabilities, dict) else {}
            if isinstance(tables_description, dict):
                for table_name in tables_description.keys():
                    name = str(table_name or "").strip().lower()
                    if name:
                        terms.add(name)
        except Exception:
            pass

        cls._cached_sql_terms = {t for t in terms if t}
        return cls._cached_sql_terms

    @staticmethod
    def fallback(query: str) -> str:
        """Fallback heuristic for routing when LLM fails."""
        q = (query or "").strip().lower()

        for term in RouterService._sql_terms():
            if " " in term:
                if term in q:
                    return "SQL"
                continue
            if re.search(rf"\b{re.escape(term)}\b", q):
                return "SQL"
        return "CHAT"

    @staticmethod
    def _is_clear_chat_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return True

        greeting_patterns = [
            r"^(hi|hello|hey)\b",
            r"^good\s+(morning|afternoon|evening)\b",
            r"^(how are you|what's up|whats up)\b",
            r"^(thanks|thank you)\b",
            r"^(ok|okay|cool|nice)\b",
        ]
        if any(re.search(pattern, q) for pattern in greeting_patterns):
            return True

        # Short conversational prompts are high-confidence CHAT.
        if len(q) <= 24 and re.fullmatch(r"[a-zA-Z\s!?.,']+", q):
            return True
        return False

    async def route(self, query: str) -> str:
        route, _usage = await self.route_with_usage(query)
        return route

    async def route_with_usage(self, query: str) -> Tuple[str, Dict[str, int]]:
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
            return "CHAT", TokenUsageService.skipped_call()

        # PRIORITY 2: Deterministic fast-path for clear SQL queries
        fallback_route = self.fallback(query)
        if fallback_route == "SQL":
            return "SQL", TokenUsageService.skipped_call()
        if fallback_route == "CHAT" and self._is_clear_chat_query(query):
            logger.info("Routing to CHAT (clear conversational query): %s", query[:50])
            return "CHAT", TokenUsageService.skipped_call()

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
            usage = TokenUsageService.from_response(
                response,
                prompt_with_toon=prompt,
                prompt_without_toon=prompt,
                toon_applied=False,
            )
            raw = str(response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw[start : end + 1])
                route = str(parsed.get("route", "")).upper()
                if route in {"SQL", "CHAT"}:
                    return route, usage
        except Exception:
            pass
        return self.fallback(query), TokenUsageService.empty()
