from langgraph.graph import StateGraph, END
from app.workflow.state import AgentState
from .router import RouterNode

# Import new modular nodes
from app.workflow.nodes.sql_node import GenerateSQLNode
from app.workflow.nodes.validation_node import ValidateSQLNode
from app.workflow.nodes.execution_node import ExecuteSQLNode
from app.workflow.nodes.response_node import ResponseNode
from app.workflow.nodes.pii_node import PIINode

# Implementations from adjacent files (still there)
from .vector_search import VectorSearchNode
from .general_chat import GeneralChatNode
from .contextualize import ContextualizeNode

def create_graph():
    # Instantiate Nodes
    router = RouterNode()
    
    # New Nodes
    sql_gen = GenerateSQLNode()
    validator = ValidateSQLNode()
    executor = ExecuteSQLNode()
    responder = ResponseNode()
    pii = PIINode()
    
    # Existing Nodes
    vector = VectorSearchNode()
    chat = GeneralChatNode()
    context = ContextualizeNode()

    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("contextualize", context.run)
    workflow.add_node("router", router.route_query)
    workflow.add_node("pii_process", pii.run)
    workflow.add_node("generate_sql", sql_gen.run)
    workflow.add_node("validate_sql", validator.run)
    workflow.add_node("execute_sql", executor.run)
    workflow.add_node("generate_response", responder.run)
    workflow.add_node("vector_search", vector.run)
    workflow.add_node("general_chat", chat.run)

    # Entry Point (Contextualize only if there is history)
    def entry_point(state: AgentState):
        if len(state.get("messages", [])) <= 1:
            return "router"
        return "contextualize"
        
    workflow.set_conditional_entry_point(
        entry_point,
        {
            "router": "router",
            "contextualize": "contextualize"
        }
    )
    workflow.add_edge("contextualize", "router")
    
    # Router Conditional
    def route_decision(state: AgentState):
        route = state.get("route", "SQL")
        if route == "SQL":
            return "pii_process"
        elif route == "VECTOR":
            return "vector_search"
        elif route == "CHAT":
            return "general_chat"
        return "pii_process" # Default

    workflow.add_conditional_edges(
        "router",
        route_decision,
        {
            "pii_process": "pii_process",
            "vector_search": "vector_search",
            "general_chat": "general_chat"
        }
    )

    # SQL Pipeline
    workflow.add_edge("pii_process", "generate_sql")
    
    def after_generation(state: AgentState):
        if state.get("sql_query") == "SKIP":
            return END
        return "validate_sql"
        
    workflow.add_conditional_edges(
        "generate_sql",
        after_generation,
        {
            "validate_sql": "validate_sql",
            END: END
        }
    )
    
    # Validation Conditional
    def after_validation(state: AgentState):
        if state.get("error"):
            # If validation failed, check retry count
            if state.get("retry_count", 0) < 3:
                return "generate_sql"
            return "generate_response"
        return "execute_sql"
        
    workflow.add_conditional_edges(
        "validate_sql",
        after_validation,
        {
            "generate_sql": "generate_sql",
            "execute_sql": "execute_sql",
            "generate_response": "generate_response"
        }
    )

    # Execution Conditional (Self-Correction)
    def after_execution(state: AgentState):
        if state.get("error"):
            # If execution failed, check retry count
            if state.get("retry_count", 0) < 3:
                return "generate_sql" # Retry with error context
            return "generate_response" # Give up and report error
        return "generate_response"

    workflow.add_conditional_edges(
        "execute_sql",
        after_execution,
        {
            "generate_sql": "generate_sql",
            "generate_response": "generate_response"
        }
    )

    # Endpoints
    workflow.add_edge("generate_response", END)
    workflow.add_edge("vector_search", END)
    workflow.add_edge("general_chat", END)

    return workflow.compile()
