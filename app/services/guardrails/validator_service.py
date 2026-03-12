from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.core.toon_service import ToonService
from app.services.guardrails.models import ValidationReport


class ValidatorService:
    _INTERNAL_TOKENS = (
        "intermediate frame",
        "evidence bundle",
        "verification report",
        "validation report",
        "token_budget",
        "available_evidence_ids",
        "allowed_actions",
    )

    @staticmethod
    def _token_budget(frame: Dict[str, Any]) -> int:
        budget = frame.get("token_budget")
        if not isinstance(budget, dict):
            return 120
        try:
            return max(24, int(budget.get("response_max") or 120))
        except Exception:
            return 120

    @classmethod
    def _contains_internal_artifacts(cls, text: str) -> bool:
        lowered = str(text or "").lower()
        if any(token in lowered for token in cls._INTERNAL_TOKENS):
            return True
        if re.search(r"\{[^{}]*:[^{}]*\}", str(text or "")):
            return True
        return False

    @staticmethod
    def _contains_prompt_leak(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(token in lowered for token in ("system prompt", "developer message", "hidden prompt"))

    @staticmethod
    def _contains_raw_sql(text: str) -> bool:
        return bool(re.search(r"\b(select|update|insert into|delete from)\b.+\b(from|set|values|where)\b", str(text or ""), flags=re.IGNORECASE))

    @staticmethod
    def _shorten_to_budget(text: str, token_budget: int) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return ""
        if ToonService.estimate_tokens(normalized) <= token_budget:
            return normalized

        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        kept: List[str] = []
        for sentence in sentences:
            candidate = " ".join(kept + [sentence]).strip()
            if not candidate:
                continue
            if ToonService.estimate_tokens(candidate) > token_budget:
                break
            kept.append(sentence)
        if kept:
            return " ".join(kept).strip()

        words = normalized.split()
        out: List[str] = []
        for word in words:
            candidate = " ".join(out + [word]).strip()
            if ToonService.estimate_tokens(candidate) > token_budget:
                break
            out.append(word)
        shortened = " ".join(out).strip()
        if shortened and shortened[-1] not in ".!?":
            shortened += "."
        return shortened

    @staticmethod
    def _generic_message(final_mode: str, frame: Dict[str, Any], verification: Dict[str, Any]) -> str:
        fallback = str(verification.get("fallback_message", "") or "").strip()
        if fallback:
            return fallback
        route = str(frame.get("route", "") or "").strip().upper()
        if final_mode == "clarify":
            return "Can you clarify the specific detail you mean?"
        if final_mode == "reject":
            return "I cannot help with that request."
        if final_mode == "abstain":
            return "I do not have enough validated information to answer that safely."
        if route == "CHAT":
            return "I can help with your configured domain workflows and data."
        return "I could not confirm that result safely."

    @classmethod
    def validate(
        cls,
        candidate: str,
        frame: Dict[str, Any],
        verification: Dict[str, Any],
        evidence: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        del evidence
        text = str(candidate or "").strip()
        final_mode = str(verification.get("status", "") or "pass").strip().lower()
        if final_mode == "pass":
            final_mode = "answer"

        reasons: List[str] = []
        redactions: List[str] = []

        if not text:
            reasons.append("empty_response")

        fallback = str(verification.get("fallback_message", "") or "").strip()
        verification_status = str(verification.get("status", "") or "pass").strip().lower()
        if verification_status != "pass" and fallback and text != fallback:
            reasons.append(f"verifier_{verification_status}")

        if cls._contains_internal_artifacts(text):
            reasons.append("internal_artifacts")
            redactions.append("internal_artifacts")
        if cls._contains_prompt_leak(text):
            reasons.append("prompt_leak")
            redactions.append("prompt_leak")
        if cls._contains_raw_sql(text):
            reasons.append("raw_sql_leak")
            redactions.append("raw_sql")

        budget = cls._token_budget(frame)
        if ToonService.estimate_tokens(text) > budget:
            reasons.append("response_too_long")

        return ValidationReport(
            status="pass" if not reasons else "fail",
            reasons=reasons,
            redactions=redactions,
            final_mode=final_mode,
        ).to_dict()

    @classmethod
    def rewrite(
        cls,
        candidate: str,
        frame: Dict[str, Any],
        verification: Dict[str, Any],
        validation: Dict[str, Any],
    ) -> str:
        reasons = [str(item or "").strip() for item in (validation.get("reasons") or []) if str(item or "").strip()]
        final_mode = str(validation.get("final_mode", "") or "answer").strip().lower()
        if any(reason.startswith("verifier_") for reason in reasons):
            return cls._generic_message(final_mode, frame, verification)

        rewritten = str(candidate or "").strip()
        if any(reason in {"internal_artifacts", "prompt_leak", "raw_sql_leak"} for reason in reasons):
            lines = []
            for line in rewritten.splitlines():
                lowered = line.lower()
                if any(token in lowered for token in cls._INTERNAL_TOKENS):
                    continue
                if "system prompt" in lowered or "developer message" in lowered:
                    continue
                if "select " in lowered or "update " in lowered or "insert into " in lowered:
                    continue
                if "{" in line and ":" in line:
                    continue
                lines.append(line)
            rewritten = " ".join(lines).strip()
            rewritten = re.sub(r"\{.*", "", rewritten, flags=re.DOTALL).strip()

        if "response_too_long" in reasons:
            rewritten = cls._shorten_to_budget(rewritten, cls._token_budget(frame))

        if not rewritten:
            rewritten = cls._generic_message(final_mode, frame, verification)
        return rewritten
