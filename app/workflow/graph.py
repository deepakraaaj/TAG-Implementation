from langgraph.graph import StateGraph, END
from .nodes import TAGNodes, AgentState
from .router import RouterNode

def create_graph():
    nodes = TAGNodes()
    router = RouterNode()
    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("contextualize", nodes.contextualize_node)
    workflow.add_node("router", router.route_query)
    workflow.add_node("pii_process", nodes.pii_node)
    workflow.add_node("generate_sql", nodes.generate_sql_node)
    workflow.add_node("validate_sql", nodes.validate_node)
    workflow.add_node("execute_sql", nodes.execute_sql_node)
    workflow.add_node("generate_response", nodes.response_node)
    workflow.add_node("vector_search", nodes.vector_search_node)
    workflow.add_node("general_chat", nodes.general_chat_node)

    # Entry Point (Contextualize First)
    workflow.set_entry_point("contextualize")
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
    # workflow.add_edge("generate_sql", "validate_sql") # Replaced by conditional below

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
            if state["retry_count"] < 3:
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
            if state["retry_count"] < 3:
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
