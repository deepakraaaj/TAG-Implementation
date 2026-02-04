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
        You are a smart router. Classify the user's query into: SQL, VECTOR, or CHAT.

        1. SQL: queries for structured data (users, tasks, assets, counts, lists) or ACTIONS.
           Examples: 
           - "List all users"
           - "How many tasks are there?"
           - "Show the next 5 facilities"
           - "Add a user"
           
        2. VECTOR: "How-to" questions, manuals, policies.
           Examples: "How do I add a user?", "What is the policy?"
           
        3. CHAT: Greetings, thanks, or general chat.
           Examples: "Hello", "Thanks", "Hi"
           
        CRITICAL: If the user asks for a count ("how many"), a list ("show me"), or status, return SQL.
        
        Return ONLY one word: SQL, VECTOR, or CHAT.
        """
        
        # Heuristic Bypass for Small Models
        intent_keywords = [
            "how many", "count", "list", "show", "what is", "what are",
            "add", "create", "assign", "update", "delete",
            "pending", "overdue", "completed", "status",
            "task", "user", "asset", "facility", "maintenance"
        ]
        
        lower_query = last_message.lower()
        if any(kw in lower_query for kw in intent_keywords):
            logger.info(f"Heuristic Match: Found keyword in '{last_message}'. Forcing SQL.")
            return {"route": "SQL"}

        prompt = f"{system_prompt}\n\nUser Query: {last_message}"
        
        try:
            response = await self.llm.ainvoke(prompt)
            raw_content = response.content.strip()
            logger.info(f"Router Raw Response: {raw_content}")
            
            # Robust parsing: Remove markdown and find keywords
            clean_content = raw_content.replace("*", "").replace("`", "").upper()
            
            if "SQL" in clean_content:
                route = "SQL"
            elif "VECTOR" in clean_content:
                route = "VECTOR"
            elif "CHAT" in clean_content:
                route = "CHAT"
            else:
                route = "CHAT" # Default
                
            # Fallback for safety
            if route not in ["SQL", "VECTOR", "CHAT"]:
                logger.warning(f"Router returned unknown route: {raw_content}. Defaulting to CHAT.")
                route = "CHAT"
                
        except Exception as e:
            logger.error(f"Router failed: {e}. Defaulting to CHAT.")
            route = "CHAT"
            
        logger.info(f"Routing query '{last_message}' to: {route}")
        return {"route": route}
