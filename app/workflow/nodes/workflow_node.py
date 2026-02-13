from typing import Dict, Any, Optional
import os
import logging
from app.workflow.state import AgentState
from app.workflow.engine.executor import WorkflowExecutor
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)

class WorkflowNode:
    def __init__(self):
        # Path to workflow definitions
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        definitions_dir = os.path.join(base_dir, "definitions")
        self.executor = WorkflowExecutor(definitions_dir)
        self.schema_service = SchemaService()

    async def run(self, state: AgentState) -> Dict:
        """
        Executes the workflow based on the current state.
        """
        messages = state["messages"]
        last_message = messages[-1].content if messages else ""
        
        # Determine session details
        metadata = state.get("metadata", {})
        session_id = metadata.get("session_id") or state.get("workflow_session_id", "default_session")
        user_id = metadata.get("user_id", "1") # Default user ID
        user_role = state.get("metadata", {}).get("user_role", "user")
        
        # Determine which workflow to run
        # If we are already in a workflow, we should continue it.
        # If not, we might look for a workflow_id in the state (set by router or intent analysis)
        
        active_workflow_id = await self.executor.get_active_workflow(session_id)
        
        # 1. Explicit Cancellation Handling
        normalized_input = last_message.strip().lower()
        cancel_commands = {"cancel", "stop", "reset", "exit"}
        if active_workflow_id and normalized_input in cancel_commands:
            logger.info(f"Cancellation command '{normalized_input}' received for session {session_id}. Clearing workflow {active_workflow_id}.")
            await self.executor.cancel(active_workflow_id, session_id)
            from langchain_core.messages import AIMessage
            return {
                "messages": [AIMessage(content="Workflow canceled. How else can I help you today?")],
                "route": "CHAT" # Switch to CHAT to prevent re-entering workflow
            }

        target_workflow_id = state.get("intent_analysis", {}).get("workflow_id")
        
        # Priority: Active Workflow > Requested Workflow
        workflow_id = active_workflow_id or target_workflow_id
        
        # If router sent here but we don't have a workflow ID, mapping needs to happen.
        if not workflow_id and state.get("route") == "WORKFLOW":
             logger.warning(f"Router sent route WORKFLOW for session {session_id}, but no workflow_id was identified.")
             return {"error": "No workflow identified for this request. Please try again or rephrase."}

        if not workflow_id:
            return {"error": "No workflow identified."}

        logger.info(f"Executing workflow {workflow_id} for session {session_id}")
        
        turn = await self.executor.execute(
            workflow_id=workflow_id,
            session_id=session_id,
            user_id=str(user_id),
            user_role=user_role,
            user_input=last_message,
            services={"schema_service": self.schema_service}
        )
        
        from langchain_core.messages import AIMessage
        return {
            "messages": [AIMessage(content=turn.reply)], # Add reply to messages
            # Store workflow state info if needed
            "workflow_payload": turn.payload.model_dump() if turn.payload else None
        }
