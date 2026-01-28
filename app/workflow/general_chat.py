from typing import Dict
from langchain_core.messages import AIMessage
from ..config import get_settings
# from langchain_groq import ChatGroq
import logging
import os

logger = logging.getLogger(__name__)
settings = get_settings()

class GeneralChatNode:
    def __init__(self):
        # Use a fast chat model
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0.7 
        )

    async def run(self, state: Dict) -> Dict:
        """
        Handles general chit-chat and greetings.
        """
        logger.info("Entering general_chat_node")
        messages = state["messages"]
        last_message = messages[-1].content
        
        prompt = f"""
        You are a friendly and helpful facility management assistant called 'LightningBot'.
        Engage in polite conversation with the user.
        Do not make up facts about the facility or database.
        If the user asks something you don't know, politely suggest they ask about tasks, users, or assets.
        
        User Message: {last_message}
        """
        
        response = await self.llm.ainvoke(prompt)
        return {"messages": [response]}
