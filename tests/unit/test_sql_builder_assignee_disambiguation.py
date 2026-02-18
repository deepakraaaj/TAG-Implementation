from app.assistant.nodes.sql_builder_node import SQLBuilderNode


def test_assignee_exact_choice_does_not_reprompt():
    node = SQLBuilderNode()
    node._lookup_user_candidates = lambda value, metadata: [
        ("Nirmala S", "assignee=Nirmala S"),
        ("Nirmalraj S", "assignee=Nirmalraj S"),
    ]
    node._last_user_lookup_used_fuzzy = False

    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "Nirmala S", "scheduled_date": "today"},
        {},
    )

    assert prompt is None
    assert filters.get("assignee") == "Nirmala S"


def test_assignee_me_resolves_without_prompt():
    node = SQLBuilderNode()
    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "me", "scheduled_date": "today"},
        {"user_name": "Vinothini V"},
    )
    assert prompt is None
    assert filters.get("assignee") == "Vinothini V"


def test_assigned_to_current_user_resolves_without_prompt():
    node = SQLBuilderNode()
    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assigned_to": "current_user", "scheduled_date": "today"},
        {"user_name": "Vinothini V"},
    )
    assert prompt is None
    assert filters.get("assignee") == "Vinothini V"
