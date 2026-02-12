import json
import logging
import os

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.workflow.state import AgentState

logger = logging.getLogger(__name__)
settings = get_settings()


class IntentAnalysisNode:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )

    async def run(self, state: AgentState):
        query = state.get("rewritten_query") or state["messages"][-1].content
        prompt = f"""
Analyze this user query and return ONLY valid JSON with:
{{
  "intent_type": "listing|aggregation|lookup|mutation|unknown",
  "entities": ["..."],
  "filters": ["..."],
  "metrics": ["..."]
}}

User query: {query}
"""
        try:
            response = await self.llm.ainvoke(prompt, max_tokens=120)
            raw = response.content.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(raw[start : end + 1])
                return {"intent_analysis": parsed}
        except Exception as e:
            logger.warning("Intent analysis failed: %s", e)
        return {
            "intent_analysis": {
                "intent_type": "unknown",
                "entities": [],
                "filters": [],
                "metrics": [],
            }
        }
