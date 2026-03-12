from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import AIMessage


class GuardrailNode:
    def __init__(
        self,
        intermediate_service: Any,
        evidence_service: Any,
        verifier_service: Any,
        validator_service: Any,
        metrics_service: Any | None = None,
    ):
        self.intermediate = intermediate_service
        self.evidence = evidence_service
        self.verifier = verifier_service
        self.validator = validator_service
        self.metrics = metrics_service

    def _record_verifier(self, route: str, verification: Dict[str, Any]) -> None:
        if self.metrics is None:
            return
        recorder = getattr(self.metrics, "record_guardrail_verifier", None)
        if callable(recorder):
            recorder(route=route, status=str(verification.get("status", "pass") or "pass"))

    def _record_validator_failure(self, route: str, validation: Dict[str, Any]) -> None:
        if self.metrics is None:
            return
        recorder = getattr(self.metrics, "record_guardrail_validator_failure", None)
        if not callable(recorder):
            return
        reasons = validation.get("reasons")
        if isinstance(reasons, list) and reasons:
            recorder(route=route, reason=str(reasons[0] or "validation_failed"))
        else:
            recorder(route=route, reason="validation_failed")

    def _record_final_mode(self, route: str, final_mode: str) -> None:
        if self.metrics is None:
            return
        if final_mode == "clarify":
            recorder = getattr(self.metrics, "record_guardrail_clarify", None)
            if callable(recorder):
                recorder(route=route)
        elif final_mode == "abstain":
            recorder = getattr(self.metrics, "record_guardrail_abstain", None)
            if callable(recorder):
                recorder(route=route)

    async def run(self, state: Dict) -> Dict:
        messages = state.get("messages") or []
        candidate = str(messages[-1].content) if messages else ""
        if not candidate:
            return {}

        frame = state.get("intermediate_frame")
        if not isinstance(frame, dict):
            frame = self.intermediate.build(state)

        evidence = self.evidence.assemble(state, frame=frame)
        evidence_ids = [
            str(item.get("id", "")).strip()
            for item in (evidence.get("items") or [])
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
        frame = dict(frame)
        frame["available_evidence_ids"] = evidence_ids

        verification = self.verifier.verify(candidate, frame, evidence, state)
        route = str(frame.get("route", "") or state.get("route", "") or "CHAT").strip().upper() or "CHAT"
        self._record_verifier(route, verification)

        guarded_candidate = str(candidate or "").strip()
        if bool(verification.get("rewrite_needed")) and str(verification.get("fallback_message", "")).strip():
            guarded_candidate = str(verification.get("fallback_message") or "").strip()
        elif str(verification.get("status", "") or "pass").strip().lower() != "pass":
            guarded_candidate = self.validator.rewrite(
                guarded_candidate,
                frame,
                verification,
                {"reasons": [f"verifier_{verification.get('status', 'abstain')}"], "final_mode": verification.get("status", "abstain")},
            )

        validation = self.validator.validate(guarded_candidate, frame, verification, evidence)
        if str(validation.get("status", "")).strip().lower() == "fail":
            self._record_validator_failure(route, validation)
            rewritten = self.validator.rewrite(guarded_candidate, frame, verification, validation)
            guarded_candidate = str(rewritten or "").strip()
            validation = self.validator.validate(guarded_candidate, frame, verification, evidence)

        final_mode = str(validation.get("final_mode", "") or "answer").strip().lower() or "answer"
        if str(validation.get("status", "")).strip().lower() == "fail":
            guarded_candidate = self.validator.rewrite("", frame, verification, validation)
            validation = self.validator.validate(guarded_candidate, frame, verification, evidence)
            final_mode = str(validation.get("final_mode", "") or final_mode).strip().lower() or final_mode

        self._record_final_mode(route, final_mode)
        if guarded_candidate != candidate:
            messages = list(messages[:-1]) + [AIMessage(content=guarded_candidate)]

        return {
            "messages": messages,
            "intermediate_frame": frame,
            "evidence_bundle": evidence,
            "verification_report": verification,
            "validation_report": validation,
        }
