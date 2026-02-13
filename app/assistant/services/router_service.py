import json
import os
import re

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

settings = get_settings()


class RouterService:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )

    @staticmethod
    def fallback(query: str) -> str:
        q = (query or "").strip().lower()
        if re.search(r"\b(task|asset|user|facility|select|insert|update|create|add|edit|modify|show|list|count|get|find)\b", q):
            return "SQL"
        return "CHAT"

    async def route(self, query: str) -> str:
        prompt = f"""
Classify user message as SQL or CHAT.
Return only JSON: {{"route":"SQL|CHAT"}}
User: {query}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=2,
                backoff_seconds=0.3,
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
