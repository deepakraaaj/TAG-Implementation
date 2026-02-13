from langgraph.graph import END, StateGraph

from app.assistant.state import AgentState
from app.assistant.nodes.chat_node import ChatNode
from app.assistant.nodes.intent_node import IntentNode
from app.assistant.nodes.response_node import ResponseNode
from app.assistant.nodes.router_node import RouterNode
from app.assistant.nodes.sql_builder_node import SQLBuilderNode
from app.assistant.nodes.sql_execute_node import SQLExecuteNode
from app.assistant.nodes.sql_validate_node import SQLValidateNode


def create_graph():
    router = RouterNode()
    chat = ChatNode()
    intent = IntentNode()
    sql_build = SQLBuilderNode()
    sql_validate = SQLValidateNode()
    sql_execute = SQLExecuteNode()
    responder = ResponseNode()

    graph = StateGraph(AgentState)
    graph.add_node("route", router.run)
    graph.add_node("chat", chat.run)
    graph.add_node("intent", intent.run)
    graph.add_node("sql_build", sql_build.run)
    graph.add_node("sql_validate", sql_validate.run)
    graph.add_node("sql_execute", sql_execute.run)
    graph.add_node("respond", responder.run)

    graph.set_entry_point("route")

    graph.add_conditional_edges(
        "route",
        lambda state: "chat" if state.get("route") == "CHAT" else "intent",
        {"chat": "chat", "intent": "intent"},
    )

    graph.add_edge("intent", "sql_build")
    graph.add_conditional_edges(
        "sql_build",
        lambda state: END if state.get("sql_query") == "SKIP" else "sql_validate",
        {"sql_validate": "sql_validate", END: END},
    )
    graph.add_conditional_edges(
        "sql_validate",
        lambda state: "respond" if state.get("error") else "sql_execute",
        {"respond": "respond", "sql_execute": "sql_execute"},
    )
    graph.add_edge("sql_execute", "respond")

    graph.add_edge("chat", END)
    graph.add_edge("respond", END)

    return graph.compile()
