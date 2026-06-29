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


class _KnowledgeOnlyDomain:
    config = {"bot_name": "Project Assistant"}
    description = "knowledge-only domain"

    @staticmethod
    def get_assistant_prompt_config():
        return {
            "role_description": "a practical assistant",
            "template": "You are {bot_name}. User query: {query}",
            "suggested_queries": [],
        }

    @staticmethod
    def get_capabilities():
        return {}

    @staticmethod
    def get_domain_knowledge_config():
        return {
            "scope": "warehouse operations including orders, staff, and sites",
            "primary_entities": ["orders", "staff", "sites"],
            "business_terms": {},
            "example_queries": ["show open orders"],
            "reasoning_profile": {
                "name": "ClearTM canonical AI reasoning",
                "behavior_summary": (
                    "Direct answer first, one clarification if needed, and abstain instead of guessing when validated evidence is missing."
                ),
                "rules": [
                    "frame only",
                    "evidence first",
                    "answer directly",
                    "one clarification if blocked",
                    "say when evidence is missing",
                    "no invented data or causes",
                    "no persona",
                    "no internal reasoning trace",
                    "plain text",
                ],
                "response_modes": {
                    "default": "direct answer, 1-4 short sentences",
                    "help": "help <=5 lines, <=3 examples",
                },
                "evidence_sources": ["sql_rowset", "domain_config"],
                "clarification_policy": "Ask one targeted clarification question when blocked.",
                "abstention_policy": "If evidence is missing, abstain.",
            },
        }


class _KnowledgeOnlyIntelligence:
    def __init__(self):
        self.domain = _KnowledgeOnlyDomain()

    def get_help_response(self) -> str:
        return "help"

    @staticmethod
    def domain_scope() -> str:
        return "warehouse operations including orders, staff, and sites"

    @staticmethod
    def is_off_topic(_query: str) -> bool:
        return False

    @staticmethod
    def handle_inappropriate(_query: str) -> str:
        return "redirect"


def test_chat_node_returns_fallback_message_on_connection_error():
    node = ChatNode()
    node.llm = _FailingLLM()

    state = {"messages": [type("M", (), {"content": "hello"})()]}
    result = asyncio.run(node.run(state))

    assert "messages" in result
    assert len(result["messages"]) == 1
    assert "assistant" in str(result["messages"][0].content).lower()


def test_chat_node_uses_domain_configured_compact_guardrail_prompt(monkeypatch):
    captured = {}

    async def _fake_ainvoke(_llm, prompt, **_kwargs):
        captured["prompt"] = prompt
        return _LLMResponse("Compact reply.")

    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _fake_ainvoke)

    node = ChatNode(llm=object())
    prompt_cfg = node.intelligence.domain.get_assistant_prompt_config()
    compact_cfg = dict(prompt_cfg.get("compact_reasoning") or {})
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
    assert "Frame:" in prompt
    assert "evidence=domain_config" in prompt
    assert str(compact_cfg.get("engine_label", "")).strip() in prompt
    assert "no persona" in prompt
    assert "no examples unless help was requested" in prompt.lower()
    assert "help=" in prompt
    assert "Recent conversation context:" not in prompt
    assert len(prompt) < len(legacy_prompt)
    assert str(result["messages"][0].content) == "Compact reply."


def test_chat_node_non_help_prompt_omits_example_suggestions(monkeypatch):
    captured = {}

    async def _fake_ainvoke(_llm, prompt, **_kwargs):
        captured["prompt"] = prompt
        return _LLMResponse("Direct reply.")

    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _fake_ainvoke)

    node = ChatNode(llm=object())
    state = {
        "messages": [type("M", (), {"content": "why is the task delayed"})()],
        "intermediate_frame": {
            "intent": "general",
            "entities": ["tasks"],
            "filters": {},
            "unknowns": [],
            "required_evidence": ["explicit_cause"],
            "allowed_actions": ["answer", "clarify", "abstain"],
            "token_budget": {"response_max": 80},
            "notes": {"question_type": "causal"},
        },
    }

    asyncio.run(node.run(state))

    prompt = captured["prompt"]
    assert "help=" not in prompt
    assert "Mode:no cause inference" in prompt


def test_chat_node_prefers_generated_domain_knowledge_when_prompt_config_is_sparse(monkeypatch):
    captured = {}

    async def _fake_ainvoke(_llm, prompt, **_kwargs):
        captured["prompt"] = prompt
        return _LLMResponse("Direct reply.")

    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _fake_ainvoke)

    node = ChatNode(llm=object(), intelligence=_KnowledgeOnlyIntelligence())
    state = {
        "messages": [type("M", (), {"content": "summarize the order workflow briefly"})()],
        "intermediate_frame": {
            "intent": "help",
            "entities": ["orders"],
            "filters": {},
            "unknowns": [],
            "required_evidence": ["domain_config"],
            "allowed_actions": ["answer", "clarify", "abstain"],
            "token_budget": {"response_max": 80},
            "notes": {"question_type": "help"},
        },
    }

    asyncio.run(node.run(state))

    prompt = captured["prompt"]
    assert "ClearTM canonical AI reasoning" in prompt
    assert "Scope: warehouse operations including orders, staff" in prompt
    assert "help=show open orders" in prompt


def test_chat_node_treats_colloquial_capability_prompt_as_help(monkeypatch):
    async def _unexpected_ainvoke(*_args, **_kwargs):
        raise AssertionError("LLM should not be called for colloquial help prompts")

    monkeypatch.setattr(chat_node_module, "ainvoke_with_retry", _unexpected_ainvoke)

    node = ChatNode(llm=object(), intelligence=_KnowledgeOnlyIntelligence())
    state = {"messages": [type("M", (), {"content": "Heyy what you can do for me"})()]}

    result = asyncio.run(node.run(state))

    assert "assistant" in str(result["messages"][0].content).lower()
