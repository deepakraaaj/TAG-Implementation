import asyncio

from langchain_core.messages import AIMessage

from app.assistant.nodes.core.guardrail_node import GuardrailNode
from app.services.guardrails import EvidenceService, IntermediateService, ValidatorService, VerifierService


def _guardrail_node() -> GuardrailNode:
    return GuardrailNode(
        intermediate_service=IntermediateService(),
        evidence_service=EvidenceService(),
        verifier_service=VerifierService(),
        validator_service=ValidatorService(),
    )


def test_guardrail_node_clarifies_missing_referent():
    node = _guardrail_node()
    state = {
        "route": "CHAT",
        "messages": [AIMessage(content="It is still pending.")],
        "intermediate_frame": {
            "route": "CHAT",
            "current_message": "what about it?",
            "unknowns": ["referent"],
            "required_evidence": [],
            "notes": {"question_type": "general"},
            "token_budget": {"response_max": 80},
        },
    }

    result = asyncio.run(node.run(state))

    assert str(result["messages"][-1].content) == "What does 'it' refer to in your request?"
    assert result["verification_report"]["status"] == "clarify"


def test_guardrail_node_abstains_on_unverified_chat_data_claim():
    node = _guardrail_node()
    state = {
        "route": "CHAT",
        "messages": [AIMessage(content="You have 7 overdue tasks today.")],
        "intermediate_frame": {
            "route": "CHAT",
            "current_message": "how many tasks do I have today?",
            "unknowns": [],
            "required_evidence": ["sql_rowset"],
            "notes": {"question_type": "count", "requires_data_evidence": True},
            "token_budget": {"response_max": 80},
        },
    }

    result = asyncio.run(node.run(state))

    assert str(result["messages"][-1].content) == (
        "I do not have enough validated data to answer that directly. Ask me to list or count the relevant records."
    )
    assert result["verification_report"]["status"] == "abstain"


def test_guardrail_node_rewrites_mismatched_sql_count_message():
    node = _guardrail_node()
    state = {
        "route": "SQL",
        "sql_query": "SELECT asset_id FROM asset WHERE company_id = 1 LIMIT 100;",
        "row_count": 10,
        "rows_preview": [{"asset_id": 1}],
        "messages": [AIMessage(content="Found 5 record(s).")],
        "intermediate_frame": {
            "route": "SQL",
            "current_message": "show assets",
            "unknowns": [],
            "required_evidence": ["sql_rowset"],
            "notes": {"question_type": "lookup", "requires_data_evidence": True},
            "token_budget": {"response_max": 80},
        },
    }

    result = asyncio.run(node.run(state))

    assert str(result["messages"][-1].content) == "Found 10 record(s)."
    assert result["verification_report"]["rewrite_needed"] is True
