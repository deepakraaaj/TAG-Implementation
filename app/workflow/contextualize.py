from typing import Dict
from ..config import get_settings
from app.services.contextualization_service import ContextualizationService
from app.services.llm_retry_service import ainvoke_with_retry
from app.services.query_understanding_service import QueryUnderstandingService
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
        self.query_understanding = QueryUnderstandingService()

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

        understanding = await self.query_understanding.analyze(str(last_message.content), messages)
        refinement_only = self.contextualization_service.is_refinement_only_query(str(last_message.content))

        if (
            understanding.get("is_self_contained")
            and float(understanding.get("confidence", 0.0)) >= 0.7
            and not refinement_only
        ):
            logger.info(
                "Skipping contextualization via understanding service for self-contained query: '%s'",
                last_message.content,
            )
            return {
                "rewritten_query": str(last_message.content).strip(),
                "query_understanding": understanding,
            }

        if (
            self.contextualization_service.is_self_contained_operational_query(str(last_message.content))
            and not refinement_only
        ):
            logger.info("Skipping contextualization for self-contained operational query: '%s'", last_message.content)
            return {
                "rewritten_query": str(last_message.content).strip(),
                "query_understanding": understanding,
            }

        deterministic = self.contextualization_service.infer_deterministic_rewrite(messages)
        if deterministic:
            logger.info(f"Deterministic contextualization applied: '{last_message.content}' -> '{deterministic}'")
            return {
                "rewritten_query": deterministic,
                "query_understanding": await self.query_understanding.analyze(deterministic, messages),
            }

        history_str = self.contextualization_service.format_history(messages)
        logger.info(f"Contextualizer Prompt History:\n{history_str}")

        prompt = self.contextualization_service.build_prompt(history_str, str(last_message.content))

        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                max_tokens=80,
                attempts=3,
                backoff_seconds=0.3,
                validator=lambda r: bool(str(getattr(r, "content", "")).strip()),
                task_name="contextualizer_llm",
            )
            rewritten_text = str(response.content).strip()
        except Exception as e:
            logger.warning("Contextualization LLM failed after retries: %s", e)
            rewritten_text = str(last_message.content).strip()

        if not rewritten_text:
            rewritten_text = str(last_message.content).strip()
        
        logger.info(f"Contextualized: '{last_message.content}' -> '{rewritten_text}'")

        return {
            "rewritten_query": rewritten_text,
            "query_understanding": await self.query_understanding.analyze(rewritten_text, messages),
        }
