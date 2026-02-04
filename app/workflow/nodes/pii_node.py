import logging
from langchain_core.messages import HumanMessage
from app.services.pii_service import PIIService
from app.workflow.state import AgentState

logger = logging.getLogger(__name__)

class PIINode:
    def __init__(self):
        self.pii_service = PIIService()

    async def run(self, state: AgentState):
        """
        Redacts PII from user messages.
        """
        logger.info("Entering pii_node")
        last_message = state["messages"][-1]
        if isinstance(last_message, HumanMessage):
            original_content = last_message.content
            sanitized_content = self.pii_service.sanitize(original_content)
            # Update the message content in place or append a note
            last_message.content = sanitized_content
            
        return {"messages": state["messages"]}
