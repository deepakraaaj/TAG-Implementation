"""Deterministic tests for the REMP NL -> flow-trigger layer.

This is the leg that decides whether a natural-language message launches a
guided flow (create/assign/update/checklist/schedule) vs. falls through to a
normal query. The decision is regex-based and does NOT need the LLM:

* DomainRegistry.is_flow_candidate(message, table) -- REMP rules + config
* ChatService._binding_message_matches(binding, message) -- per-binding intent

A live LLM smoke test confirmed the same routing end-to-end, but these tests
pin the deterministic core so it can't silently regress.
"""

import pytest

from app.domains.registry import DomainRegistry
from app.services.chat.service import ChatService


@pytest.fixture(scope="module", autouse=True)
def _fresh_remp_domain():
    DomainRegistry.reset_cache()
    yield
    DomainRegistry.reset_cache()


def _is_candidate(message: str, table: str) -> bool:
    with DomainRegistry.use_domain("REMP") as domain:
        return domain.is_flow_candidate(message, table)


# ---------------------------------------------------------------------------
# Action phrasings SHOULD launch a flow (incl. "help me ..." — regression)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "create a task",
        "create task",
        "add a task",
        "new task",
        "please create a task for me",
        "help me to create a task",      # regression: 'help' must not block this
        "help me create a new task",
    ],
)
def test_create_task_phrasings_are_flow_candidates(message):
    assert _is_candidate(message, "task_transaction") is True


@pytest.mark.parametrize(
    "message",
    [
        "assign task 5 to john",
        "reassign task 5",
        "give task 5 to maria",
        "help me assign task 5",         # regression: 'help' must not block this
        "update task status",
        "mark task 5 done",
        "complete task 5",
        "close task 5",
    ],
)
def test_assign_and_update_task_phrasings_are_flow_candidates(message):
    assert _is_candidate(message, "task_transaction") is True


@pytest.mark.parametrize(
    "message",
    ["complete checklist 12", "update checklist", "finish checklist 3", "mark checklist done"],
)
def test_checklist_phrasings_are_flow_candidates(message):
    assert _is_candidate(message, "check_list_transaction") is True


@pytest.mark.parametrize(
    "message",
    ["create schedule", "schedule maintenance", "create recurring maintenance"],
)
def test_schedule_phrasings_are_flow_candidates(message):
    assert _is_candidate(message, "scheduler_task_details") is True


# ---------------------------------------------------------------------------
# Read / conversational phrasings must NOT launch a flow
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "show tasks",
        "list all tasks",
        "how many tasks are open",
        "what tasks are due today",
        "count tasks",
        "view task 5",
        "help",            # bare help is conversational, not a create request
        "hello there",
        "",
    ],
)
def test_read_and_conversational_phrasings_are_not_flow_candidates(message):
    assert _is_candidate(message, "task_transaction") is False


# ---------------------------------------------------------------------------
# ChatService._binding_message_matches (per-binding intent regex)
# ---------------------------------------------------------------------------

def test_binding_with_no_intent_matches_any_nonempty_message():
    assert ChatService._binding_message_matches({"flow_id": "x"}, "anything at all") is True


def test_binding_single_string_intent_matches_and_rejects():
    binding = {"flow_id": "assign_task", "intent": r"\bassign\s+task\b"}
    assert ChatService._binding_message_matches(binding, "please assign task 5") is True
    assert ChatService._binding_message_matches(binding, "show me the tasks") is False


def test_binding_list_intent_matches_any_pattern():
    binding = {
        "flow_id": "update_task_status",
        "intent": [r"\bcomplete\s+task\b", r"\bclose\s+task\b", r"\bmark\s+task\b"],
    }
    assert ChatService._binding_message_matches(binding, "close task 9") is True
    assert ChatService._binding_message_matches(binding, "mark task 9 as done") is True
    assert ChatService._binding_message_matches(binding, "create a new task") is False


def test_binding_is_case_insensitive():
    binding = {"flow_id": "assign_task", "intent": r"\bassign\s+task\b"}
    assert ChatService._binding_message_matches(binding, "ASSIGN TASK to bob") is True


def test_binding_empty_message_does_not_match_a_pattern():
    binding = {"flow_id": "assign_task", "intent": r"\bassign\s+task\b"}
    assert ChatService._binding_message_matches(binding, "") is False
