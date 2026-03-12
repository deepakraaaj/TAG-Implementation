import asyncio

from app.assistant.nodes.core import chat_node as chat_node_module
from app.assistant.nodes.core.chat_node import ChatNode


class _FailingLLM:
    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError("Connection error.")


class _LLMResponse:
    def __init__(self, content: str):
        self.content = content
        self.response_metadata = {"token_usage": {}}


def test_chat_node_returns_fallback_message_on_connection_error():
    node = ChatNode()
    node.llm = _FailingLLM()

    state = {"messages": [type("M", (), {"content": "hello"})()]}
    result = asyncio.run(node.run(state))

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert "temporary connection issue" in str(result["messages"][0].content).lower()


def test_chat_node_uses_compact_guardrail_prompt(monkeypatch):
    captured = {}

    async def _fake_ainvoke(_llm, prompt, **_kwargs):
        captured["prompt"] = prompt
        return _LLMResponse("Compact reply.")

    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _fake_ainvoke)

    node = ChatNode(llm=object())
    state = {
        "messages": [type("M", (), {"content": "summarize the task workflow briefly"})()],
        "metadata": {
            "_recent_conversation": [
                {"role": "user", "content": "show my tasks"},
                {"role": "assistant", "content": "I can list them."},
            ]
        },
        "intermediate_frame": {
            "intent": "help",
            "entities": ["tasks"],
            "filters": {},
            "unknowns": [],
            "required_evidence": ["domain_config"],
            "allowed_actions": ["answer", "clarify", "abstain"],
            "token_budget": {"response_max": 120},
            "session_summary": ["User: show my tasks", "Assistant: I can list them."],
            "notes": {"question_type": "help"},
        },
    }

    result = asyncio.run(node.run(state))

    prompt = captured["prompt"]
    legacy_prompt = node._build_legacy_chat_prompt(
        node.intelligence.domain.config.get("bot_name", "Assistant"),
        node.intelligence.domain.description,
        "summarize the task workflow briefly",
        recent_context="User: show my tasks\nAssistant: I can list them.",
    )
    assert "Context frame:" in prompt
    assert "required_evidence=domain_config" in prompt
    assert "Recent conversation context:" not in prompt
    assert len(prompt) < len(legacy_prompt)
    assert str(result["messages"][0].content) == "Compact reply."
