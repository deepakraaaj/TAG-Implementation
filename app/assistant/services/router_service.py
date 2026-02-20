import json
import logging
import os
import re
from typing import Set

from langchain_openai import ChatOpenAI

from app.assistant.services.manifest_catalog import ManifestCatalog
from app.config import get_settings
from app.domains.registry import DomainRegistry
from app.services.llm_retry_service import ainvoke_with_retry

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
