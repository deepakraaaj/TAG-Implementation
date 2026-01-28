from typing import Dict, Any
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from ..config import get_settings
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
            
        # Get history (last 4 messages excluding the current one)
        # We need to construct a chat history for the prompt
        # Actually, simpler: Just send the messages to the LLM with a system prompt
        
        # Optimize: Only use last 2 turns for context
        history_msgs = messages[:-1][-4:] 
        
        history_str = ""
        for m in history_msgs:
            role = "User" if isinstance(m, HumanMessage) else "Assistant"
            content = m.content
            if len(content) > 200: # Truncate history items
                content = content[:200] + "..."
            history_str += f"{role}: {content}\n"
            
        system_prompt = """
        You are a query rewriting assistant.
        Your task is to REWRITE the User's last question to be self-contained, resolving any pronouns or ambiguous references using the Chat History.
        
        Rules:
        1. If the user says "it", "them", "those", "the list", etc., replace them with the specific nouns from history.
        2. Keep the intent exactly the same.
        3. If the question is already self-contained (e.g. "Hello", "Show all users"), return it UNCHANGED.
        4. Return ONLY the rewritten question. No formatting.
        """
        
        prompt = f"""
        {system_prompt}
        
        Chat History:
        {history_str}
        
        User's Last Question: {last_message.content}
        
        Rewritten Question:
        """
        
        response = await self.llm.ainvoke(prompt)
        rewritten_text = response.content.strip()
        
        logger.info(f"Contextualized: '{last_message.content}' -> '{rewritten_text}'")
        
        # Replace the last message content with the rewritten one?
        # Or store it separately?
        # To make it seamless for downstream nodes (Router, SQL), standardizing on LAST message is best.
        # But we should preserve the original for the UI? 
        # State in LangGraph is usually ephemeral or cumulative.
        # If we mod the message content here, the Router and SQL Node will see the new text.
        # The user will see their original text in the UI (client side).
        # BE output usually mirrors input?
        # Let's update the message in state.
        
        # We need to return a diff. 
        # LangGraph behavior: returning {"messages": [new_msg]} APPENDS.
        # We want to MODIFY the last message.
        # This depends on the reducer. If standard 'add_messages', it appends.
        # So we probably shouldn't modify 'messages' directly if we can't replace.
        
        # Alternative: Add a 'rewritten_query' key to state.
        # And update downstream nodes to look for 'rewritten_query' OR 'messages[-1]'.
        
        return {"rewritten_query": rewritten_text}
