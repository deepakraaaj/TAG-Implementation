from langgraph.graph import END, StateGraph

from app.assistant.state import AgentState


def create_graph(
    *,
    router_node,
    intermediate_node,
    chat_node,
    guardrail_node,
    report_node,
    intent_node,
    sql_builder_node,
    sql_validate_node,
    sql_execute_node,
    response_node,
):
    graph = StateGraph(AgentState)
    graph.add_node("route", router_node.run)
    graph.add_node("intermediate", intermediate_node.run)
    graph.add_node("chat", chat_node.run)
    graph.add_node("guardrail", guardrail_node.run)
    graph.add_node("report", report_node.run)
    graph.add_node("intent", intent_node.run)
    graph.add_node("sql_build", sql_builder_node.run)
    graph.add_node("sql_validate", sql_validate_node.run)
    graph.add_node("sql_execute", sql_execute_node.run)
    graph.add_node("respond", response_node.run)

    graph.set_entry_point("route")
    graph.add_edge("route", "intermediate")

    graph.add_conditional_edges(
        "intermediate",
        lambda state: "report" if state.get("route") == "REPORT" else ("chat" if state.get("route") == "CHAT" else "intent"),
        {"chat": "chat", "intent": "intent", "report": "report"},
    )

    graph.add_edge("report", END)
    graph.add_edge("intent", "sql_build")
    graph.add_conditional_edges(
        "sql_build",
        lambda state: "guardrail" if state.get("sql_query") == "SKIP" and state.get("messages") else ("sql_validate" if state.get("sql_query") != "SKIP" else END),
        {"sql_validate": "sql_validate", "guardrail": "guardrail", END: END},
    )
    graph.add_conditional_edges(
        "sql_validate",
        lambda state: "respond" if state.get("error") else "sql_execute",
        {"respond": "respond", "sql_execute": "sql_execute"},
    )
    graph.add_edge("sql_execute", "respond")

    graph.add_edge("chat", "guardrail")
    graph.add_edge("respond", "guardrail")
    graph.add_edge("guardrail", END)

    return graph.compile()
