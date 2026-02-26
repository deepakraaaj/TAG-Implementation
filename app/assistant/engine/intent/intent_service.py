import json
import re
from typing import Any, Dict, Optional, Tuple

from app.config import get_settings
from app.services.core.llm_retry_service import ainvoke_with_retry
from app.services.core.token_usage_service import TokenUsageService

settings = get_settings()


class IntentService:
    def __init__(self, llm: Any):
        self.llm = llm

    @staticmethod
    def fallback(query: str) -> Dict[str, Any]:
        q = (query or "").lower()
        operation = "select"
        if re.search(r"\b(insert|create|add|new)\b", q):
            operation = "insert"
        elif re.search(r"\b(update|edit|modify|change|set)\b", q):
            operation = "update"

        return {
            "operation": operation,
            "table": "",
            "filters": {},
            "fields": {},
        }

    async def analyze(self, query: str) -> Dict[str, Any]:
        intent, _usage = await self.analyze_with_usage(query, metadata=None)
        return intent

    @staticmethod
    def _token_minimization_enabled(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        raw = meta.get("token_minimization")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _looks_simple_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return True
        if len(q) <= 120:
            if re.search(r"\b(show|list|get|find|select|create|insert|update|change|modify|set|delete|remove)\b", q):
                return True
            if re.search(r"[a-z_][a-z0-9_]*\s*[:=]\s*[^,;]+", q):
                return True
        if len(q) <= 30:
            return True
        return False

    async def analyze_with_usage(
        self,
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        prompt = f"""
Return ONLY JSON with keys:
operation: select|insert|update
table: db table name or empty string
filters: object
fields: object

User query: {query}
"""
        if self._token_minimization_enabled(metadata) and self._looks_simple_query(query):
            return self.fallback(query), TokenUsageService.skipped_call()

        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_intent",
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
                parsed.setdefault("operation", "select")
                parsed.setdefault("table", "")
                parsed.setdefault("filters", {})
                parsed.setdefault("fields", {})
                if not isinstance(parsed["filters"], dict):
                    parsed["filters"] = {}
                if not isinstance(parsed["fields"], dict):
                    parsed["fields"] = {}
                return parsed, usage
        except Exception:
            pass

        return self.fallback(query), TokenUsageService.empty()
