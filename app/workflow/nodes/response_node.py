import logging
import os
import json
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from app.workflow.state import AgentState
from app.config import get_settings
from app.workflow.prompts import RESPONSE_GEN_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)
settings = get_settings()

class ResponseNode:
    def __init__(self):
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0
        )

    async def run(self, state: AgentState):
        """
        Generates the final natural language response.
        """
        logger.info("Entering response_node")
        if state.get("error"):
             return {"messages": [AIMessage(content=f"I encountered an error processing your request: {state['error']}")]}
             
        # Use structured info if available, otherwise fallback to sql_result
        if state.get("rows_preview") is not None:
            # We have structured data
            # Truncate preview for LLM context to avoid token limits (UI gets the full preview)
            llm_preview = state['rows_preview'][:5] 
            result_context = f"""
            Total Rows: {state['row_count']}
            Data Preview (Top {len(llm_preview)} for context):
            {json.dumps(llm_preview, indent=2, default=str)}
            """
        else:
            # Fallback legacy behavior
            result = state.get("sql_result", "")
            if len(result) > 4000:
                result = result[:4000] + "... (truncated)"
            result_context = result
            
        original_question = state["messages"][-1].content
        
        if state.get("row_count") is not None:
            # Provide count + tiny preview to ground the model
            count = state['row_count']
            preview = state.get('rows_preview', [])[:3]
            preview_str = json.dumps(preview, default=str)
            summary_context = f"Found {count} records. First few: {preview_str}"
        else:
            summary_context = result_context

        prompt = RESPONSE_GEN_PROMPT_TEMPLATE.format(
            original_question=original_question,
            summary_context=summary_context
        )
        
        # Hard cap on tokens to prevent looping/slowness
        response = await self.llm.ainvoke(prompt, max_tokens=100)
        
        # Extract Token Usage
        usage = response.response_metadata.get("token_usage", {})
        
        return {
            "messages": [response],
            "token_usage": usage
        }
