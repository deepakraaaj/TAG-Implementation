import os
from typing import Dict

from langchain_openai import ChatOpenAI

from app.config import get_settings

settings = get_settings()


class ChatNode:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0.4,
        )

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        response = await self.llm.ainvoke(f"You are a concise assistant. User: {query}")
        usage = response.response_metadata.get("token_usage", {})
        return {"messages": [response], "token_usage": usage}
