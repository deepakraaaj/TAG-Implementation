import asyncio

from app.assistant.nodes.core.chat_node import ChatNode


class _FailingLLM:
    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError("Connection error.")


def test_chat_node_returns_fallback_message_on_connection_error():
    node = ChatNode()
    node.llm = _FailingLLM()

    state = {"messages": [type("M", (), {"content": "hello"})()]}
    result = asyncio.run(node.run(state))

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert "temporary connection issue" in str(result["messages"][0].content).lower()
