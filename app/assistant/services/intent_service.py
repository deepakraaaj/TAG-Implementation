import json
import os
import re
from typing import Any, Dict

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

settings = get_settings()


class IntentService:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )

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
        prompt = f"""
Return ONLY JSON with keys:
operation: select|insert|update
table: db table name or empty string
filters: object
fields: object

User query: {query}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=2,
                backoff_seconds=0.3,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_intent",
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
                return parsed
        except Exception:
            pass

        return self.fallback(query)
