from unittest.mock import patch

from app.assistant.nodes.sql.sql_builder_node import SQLBuilderNode


_HEURISTICS_CONFIG = {
    "llm_skip_short_query_length": 12,
    "user_suggestion_candidate_pool_limit": 8,
    "user_suggestion_min_score": 0.85,
    "unfiltered_select_limit": 25,
    "name_matching": {
        "substring_min_length": 4,
        "prefix_min_length": 3,
        "meaningful_token_min_length": 2,
        "ratio_threshold": 0.9,
        "max_length_delta": 3,
        "contains_score": 0.96,
        "prefix_score": 0.85,
    },
}


def test_should_skip_llm_intent_uses_configured_short_query_limit():
    with patch.object(SQLBuilderNode, "_sql_builder_heuristics_config", return_value=_HEURISTICS_CONFIG):
        with patch.object(SQLBuilderNode, "_parse_kv_pairs", return_value={}):
            with patch.object(SQLBuilderNode, "_is_pure_filter_query", return_value=False):
                with patch.object(SQLBuilderNode, "_looks_like_direct_operation_query", return_value=False):
                    assert SQLBuilderNode._should_skip_llm_intent("status check", {}) is True
                    assert SQLBuilderNode._should_skip_llm_intent("status check summary", {}) is False


def test_suggest_user_options_uses_configured_min_score_and_candidate_pool():
    node = SQLBuilderNode()
    seen_limits = []

    def _fake_fallback(_metadata, limit_override=None):
        seen_limits.append(limit_override)
        return [{"label": "Mahalakshmi K", "value": "assignee=Mahalakshmi K"}]

    node._fallback_user_options = _fake_fallback

    with patch.object(SQLBuilderNode, "_sql_builder_heuristics_config", return_value=_HEURISTICS_CONFIG):
        with patch.object(SQLBuilderNode, "_is_strong_name_match", return_value=True):
            with patch.object(SQLBuilderNode, "_name_similarity_score", return_value=0.80):
                assert node._suggest_user_options("Mahalakshmi", {}, limit=6) == []

    assert seen_limits == [8]


def test_apply_unfiltered_select_limit_uses_configured_limit():
    with patch.object(SQLBuilderNode, "_sql_builder_heuristics_config", return_value=_HEURISTICS_CONFIG):
        sql = SQLBuilderNode._apply_unfiltered_select_limit("SELECT id FROM asset;")

    assert sql == "SELECT id FROM asset LIMIT 25;"
