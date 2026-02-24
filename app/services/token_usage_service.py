from __future__ import annotations

from typing import Any, Dict

from app.services.toon_service import ToonService


class TokenUsageService:
    _KEYS = (
        "llm_calls",
        "llm_calls_skipped",
        "toon_llm_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_est_with_toon",
        "prompt_tokens_est_without_toon",
        "prompt_tokens_est_saved",
    )

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    @classmethod
    def empty(cls) -> Dict[str, int]:
        return {}

    @classmethod
    def _compact(cls, payload: Dict[str, int]) -> Dict[str, int]:
        return {k: int(v) for k, v in (payload or {}).items() if cls._to_int(v) != 0}

    @classmethod
    def normalize_provider_usage(cls, usage: Any) -> Dict[str, int]:
        payload = dict(usage or {}) if isinstance(usage, dict) else {}
        prompt_tokens = cls._to_int(payload.get("prompt_tokens") or payload.get("input_tokens"))
        completion_tokens = cls._to_int(payload.get("completion_tokens") or payload.get("output_tokens"))
        total_tokens = cls._to_int(payload.get("total_tokens"))
        if total_tokens <= 0:
            total_tokens = max(0, prompt_tokens + completion_tokens)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @classmethod
    def skipped_call(cls) -> Dict[str, int]:
        return {"llm_calls_skipped": 1}

    @classmethod
    def from_response(
        cls,
        response: Any,
        *,
        prompt_with_toon: str,
        prompt_without_toon: str = "",
        toon_applied: bool = False,
    ) -> Dict[str, int]:
        usage = {key: 0 for key in cls._KEYS}
        metadata = dict(getattr(response, "response_metadata", {}) or {})
        provider = cls.normalize_provider_usage(metadata.get("token_usage"))

        estimated_with_toon = ToonService.estimate_tokens(str(prompt_with_toon or ""))
        estimated_without_toon = ToonService.estimate_tokens(
            str(prompt_without_toon or prompt_with_toon or "")
        )
        if estimated_without_toon < estimated_with_toon:
            estimated_without_toon = estimated_with_toon

        usage["llm_calls"] = 1
        usage["toon_llm_calls"] = 1 if toon_applied else 0
        usage["prompt_tokens"] = provider["prompt_tokens"]
        usage["completion_tokens"] = provider["completion_tokens"]
        usage["total_tokens"] = provider["total_tokens"]
        usage["prompt_tokens_est_with_toon"] = estimated_with_toon
        usage["prompt_tokens_est_without_toon"] = estimated_without_toon
        usage["prompt_tokens_est_saved"] = max(0, estimated_without_toon - estimated_with_toon)
        return cls._compact(usage)

    @classmethod
    def merge(cls, left: Any, right: Any) -> Dict[str, int]:
        a = dict(left or {}) if isinstance(left, dict) else {}
        b = dict(right or {}) if isinstance(right, dict) else {}
        merged = {key: 0 for key in cls._KEYS}
        for key in cls._KEYS:
            merged[key] = cls._to_int(a.get(key)) + cls._to_int(b.get(key))
        return cls._compact(merged)
