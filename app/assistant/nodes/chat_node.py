import os
import logging
from typing import Dict

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry

settings = get_settings()
logger = logging.getLogger(__name__)


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
        prompt = f"You are a concise assistant. User: {query}"
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=2,
                backoff_seconds=0.3,
                task_name="chat_node",
            )
            usage = response.response_metadata.get("token_usage", {})
            return {"messages": [response], "token_usage": usage}
        except Exception as exc:  # noqa: BLE001
            logger.error("ChatNode LLM call failed: %s", exc)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "I’m having a temporary connection issue to the model. "
                            "Please retry in a few seconds."
                        )
                    )
                ],
                "token_usage": {},
            }
