from typing import TypedDict, Annotated, List, Dict, Any, Literal
from langchain_core.messages import BaseMessage, HumanMessage
# from langchain_groq import ChatGroq
from ..config import get_settings
import logging
import os

logger = logging.getLogger(__name__)
settings = get_settings()

class RouterNode:
    def __init__(self):
        # Use a fast/cheap model for routing
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )

    async def route_query(self, state: Dict) -> Dict:
        """
        Classifies the user query into SQL, VECTOR, or CHAT.
        """
        logger.info("Entering router_node")
        messages = state["messages"]
        last_message = state.get("rewritten_query") or messages[-1].content
        logger.info(f"Routing Contextualized Query: {last_message}")
        
        system_prompt = """
        You are a smart router for a facility management chatbot.
        Classify the user's query into one of three categories:
        
        1. SQL: For queries asking about structured data (users, tasks, facilities, schedules, assets, counts, lists) OR requests to perform ACTIONS (add, create, update, insert, assigning).
           Examples: 
           - "List all users"
           - "How many tasks are overdue?"
           - "Add a new user named John"
           - "Update the status of task 123"
           - "Assign task to user 5"
           
        2. VECTOR: For queries asking about "how-to" (without asking to DO it), manuals, policies, or general knowledge about the system.
           Examples: 
           - "How do I add a user?" (Asking for instructions, not action)
           - "What involves maintenance?"
           
        3. CHAT: For greetings, compliments, or queries unrelated to the system.
           Examples: "Hello", "Thanks", "How are you?", "Who are you?"
           
        Return ONLY one word: SQL, VECTOR, or CHAT.
        """
        
        prompt = f"{system_prompt}\n\nUser Query: {last_message}"
        
        try:
            response = await self.llm.ainvoke(prompt)
            route = response.content.strip().upper()
            
            # Fallback for safety
            if route not in ["SQL", "VECTOR", "CHAT"]:
                logger.warning(f"Router returned unknown route: {route}. Defaulting to CHAT.")
                route = "CHAT"
                
        except Exception as e:
            logger.error(f"Router failed: {e}. Defaulting to CHAT.")
            route = "CHAT"
            
        logger.info(f"Routing query '{last_message}' to: {route}")
        return {"route": route}
