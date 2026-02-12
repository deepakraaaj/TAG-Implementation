from typing import Dict
from ..config import get_settings
from app.services.contextualization_service import ContextualizationService
import logging
import os

logger = logging.getLogger(__name__)
settings = get_settings()

class ContextualizeNode:
    def __init__(self):
        # Use a fast/cheap model for rephrasing (e.g. LLM_MODEL or a lighter one)
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )
        self.contextualization_service = ContextualizationService()

    async def run(self, state: Dict) -> Dict:
        """
        Rewrites the user's latest message to be self-contained based on history.
        """
        logger.info("Entering contextualize_node")
        messages = state["messages"]
        last_message = messages[-1]
        
        # If no history (just 1 message), no need to contextualize
        if len(messages) <= 1:
            return {"messages": messages}

        deterministic = self.contextualization_service.infer_deterministic_rewrite(messages)
        if deterministic:
            logger.info(f"Deterministic contextualization applied: '{last_message.content}' -> '{deterministic}'")
            return {"rewritten_query": deterministic}

        history_str = self.contextualization_service.format_history(messages)
        logger.info(f"Contextualizer Prompt History:\n{history_str}")

        prompt = self.contextualization_service.build_prompt(history_str, str(last_message.content))

        try:
            response = await self.llm.ainvoke(prompt, max_tokens=80)
            rewritten_text = str(response.content).strip()
        except Exception as e:
            logger.warning("Contextualization LLM call failed: %s", e)
            rewritten_text = str(last_message.content).strip()

        if not rewritten_text:
            rewritten_text = str(last_message.content).strip()
        
        logger.info(f"Contextualized: '{last_message.content}' -> '{rewritten_text}'")

        return {"rewritten_query": rewritten_text}
