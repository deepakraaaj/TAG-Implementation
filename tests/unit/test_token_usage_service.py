from app.services.token_usage_service import TokenUsageService


class _FakeResponse:
    response_metadata = {
        "token_usage": {
            "prompt_tokens": 21,
            "completion_tokens": 9,
            "total_tokens": 30,
        }
    }


def test_token_usage_service_from_response_populates_core_fields():
    usage = TokenUsageService.from_response(
        _FakeResponse(),
        prompt_with_toon="table[1]{id,name}: 1,Pump-1",
        prompt_without_toon='[{"id":1,"name":"Pump-1"}]',
        toon_applied=True,
    )
    assert int(usage.get("llm_calls", 0)) == 1
    assert int(usage.get("toon_llm_calls", 0)) == 1
    assert int(usage.get("prompt_tokens", 0)) == 21
    assert int(usage.get("completion_tokens", 0)) == 9
    assert int(usage.get("total_tokens", 0)) == 30
    assert int(usage.get("prompt_tokens_est_without_toon", 0)) >= int(
        usage.get("prompt_tokens_est_with_toon", 0)
    )


def test_token_usage_service_merge_sums_values():
    left = {"llm_calls": 1, "prompt_tokens_est_saved": 10}
    right = {"llm_calls": 2, "prompt_tokens_est_saved": 7}
    merged = TokenUsageService.merge(left, right)
    assert int(merged.get("llm_calls", 0)) == 3
    assert int(merged.get("prompt_tokens_est_saved", 0)) == 17
