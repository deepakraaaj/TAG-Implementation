from typing import Dict
from langchain_core.messages import AIMessage
from ..services.vector import vector_service
from ..services.toon import toon
from ..config import get_settings
# from langchain_groq import ChatGroq
import logging
import os
import json

logger = logging.getLogger(__name__)
settings = get_settings()

class VectorSearchNode:
    def __init__(self):
        # Use a model good at summarization/RAG
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        
        from langchain_openai import ChatOpenAI
        
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )
        logger.info(f"Vector Search Node initialized with LLM: {settings.LLM_BASE_URL}")

    async def run(self, state: Dict) -> Dict:
        """
        Executes semantic search and generates a response.
        """
        logger.info("Entering vector_search_node")
        messages = state["messages"]
        last_message = messages[-1].content
        
        # 1. Search Vector DB
        results = await vector_service.search_semantic(last_message)
        
        if not results:
            return {"messages": [AIMessage(content="I searched my knowledge base but couldn't find relevant information regarding your query.")]}
            
        # 2. Format Context
        # context_str = "\n\n".join([f"Source: {r.get('metadata', {}).get('title', 'Doc')}\nContent: {r['content']}" for r in results])
        
        # 3. Optimize Context (TOON)
        # Use full object encoding for maximum savings
        encoded_data = toon.encode(results)
        toon_payload = encoded_data["payload"]
        context_str = json.dumps(toon_payload)
        
        logger.info(f"TOON Savings: {encoded_data['meta']['savings']}")
        
        # 3. Generate Answer
        prompt = f"""
        You are a helpful assistant. Answer the user's question based ONLY on the following context.
        
        **Context Format (TOON)**:
        The context is compressed using Token-Oriented Object Notation.
        - `lookup`: A list of shared strings.
        - `data`: The content, where strings like `~3` refer to the value at index 3 in `lookup`.
        - You must resolve these references to understand the content.
        
        Context:
        {context_str}
        
        If the context doesn't contain the answer, say "I don't have information about that."
        
        User Question: {last_message}
        
        **Output Format**:
        Provide a helpful, natural language answer (Markdown supported).
        Do NOT output JSON or TOON format.
        """
        
        response = await self.llm.ainvoke(prompt)
        usage = response.response_metadata.get("token_usage", {})
        
        return {
            "messages": [response], 
            "toon_data": encoded_data,
            "token_usage": usage
        }
