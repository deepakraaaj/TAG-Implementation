from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


def test_assignee_exact_choice_does_not_reprompt():
    node = SQLBuilderNode()
    node._lookup_user_candidates = lambda value, metadata: [
        ("Nirmala S", "assignee=Nirmala S"),
        ("Nirmalraj S", "assignee=Nirmalraj S"),
    ]
    node._resolve_user_id_by_name = lambda value, metadata: "11784788" if value == "Nirmala S" else ""
    node._last_user_lookup_used_fuzzy = False

    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "Nirmala S", "scheduled_date": "today"},
        {},
    )

    assert prompt is None
    assert filters.get("assignee") == "Nirmala S"
    assert filters.get("assigned_user_id") == "11784788"


def test_assignee_me_resolves_without_prompt():
    node = SQLBuilderNode()
    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "me", "scheduled_date": "today"},
        {"user_name": "Vinothini V", "user_id": "11784578"},
    )
    assert prompt is None
    assert filters.get("assignee") == "Vinothini V"
    assert filters.get("assigned_user_id") == "11784578"


def test_assigned_to_current_user_resolves_without_prompt():
    node = SQLBuilderNode()
    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assigned_to": "current_user", "scheduled_date": "today"},
        {"user_name": "Vinothini V", "user_id": "11784578"},
    )
    assert prompt is None
    assert filters.get("assignee") == "Vinothini V"
    assert filters.get("assigned_user_id") == "11784578"


def test_assignee_no_match_returns_clear_message_not_wrong_disambiguation():
    node = SQLBuilderNode()
    node._lookup_user_candidates = lambda value, metadata: []
    node._resolve_user_id_by_name = lambda value, metadata: ""
    node._suggest_user_options = lambda value, metadata, limit=6: []

    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "Mahalakshmi", "scheduled_date": "today"},
        {},
    )

    assert filters.get("assignee") == "Mahalakshmi"
    assert isinstance(prompt, dict)
    assert prompt.get("sql_query") == "SKIP"
    assert "No assignee matched `Mahalakshmi`" in str(prompt.get("messages", [{}])[-1].content)
    assert "workflow_payload" not in prompt


def test_assignee_no_exact_match_suggests_similar_names():
    node = SQLBuilderNode()
    node._lookup_user_candidates = lambda value, metadata: []
    node._resolve_user_id_by_name = lambda value, metadata: ""
    node._suggest_user_options = lambda value, metadata, limit=6: [
        {"label": "Mahalakshmi K", "value": "assignee=Mahalakshmi K"},
        {"label": "Mahalakshmi Priya", "value": "assignee=Mahalakshmi Priya"},
    ]

    filters, prompt = node._maybe_disambiguate_filters(
        "task_transaction",
        {"assignee": "Mahalakshmi", "scheduled_date": "today"},
        {},
    )

    assert isinstance(prompt, dict)
    assert prompt.get("sql_query") == "SKIP"
    assert "No exact assignee match for `Mahalakshmi`" in str(prompt.get("messages", [{}])[-1].content)
    workflow = prompt.get("workflow_payload") or {}
    ui = workflow.get("ui") or {}
    labels = [str((x or {}).get("label", "")) for x in (ui.get("options") or [])]
    assert "Mahalakshmi K" in labels
    assert "Mahalakshmi Priya" in labels


def test_suggest_user_options_filters_unrelated_names():
    node = SQLBuilderNode()
    node._fallback_user_options = lambda metadata, limit_override=None: [
        {"label": "Dhanam M", "value": "assignee=Dhanam M"},
        {"label": "Mariyammal M", "value": "assignee=Mariyammal M"},
    ]

    options = node._suggest_user_options("Mahalakshmi", {}, limit=6)

    assert options == []


def test_suggest_user_options_keeps_strong_prefix_matches():
    node = SQLBuilderNode()
    node._fallback_user_options = lambda metadata, limit_override=None: [
        {"label": "Dhanam M", "value": "assignee=Dhanam M"},
        {"label": "Mahalakshmi K", "value": "assignee=Mahalakshmi K"},
        {"label": "Mahalakshmi Priya", "value": "assignee=Mahalakshmi Priya"},
    ]

    options = node._suggest_user_options("Mahalakshmi", {}, limit=6)
    labels = [str((x or {}).get("label", "")) for x in options]

    assert "Dhanam M" not in labels
    assert "Mahalakshmi K" in labels
    assert "Mahalakshmi Priya" in labels
