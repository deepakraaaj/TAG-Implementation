from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal


VerificationStatus = Literal["pass", "clarify", "abstain", "reject"]
ValidationStatus = Literal["pass", "fail"]


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            compacted = _compact(item)
            if compacted in (None, "", [], {}):
                continue
            out[str(key)] = compacted
        return out
    if isinstance(value, list):
        items = [_compact(item) for item in value]
        return [item for item in items if item not in (None, "", [], {})]
    return value


@dataclass
class TokenBudget:
    prompt_max: int
    response_max: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


ROUTE_TOKEN_BUDGETS: Dict[str, TokenBudget] = {
    "CHAT": TokenBudget(prompt_max=500, response_max=300),
    "SQL": TokenBudget(prompt_max=1200, response_max=150),
    "REPORT": TokenBudget(prompt_max=1000, response_max=400),
    "DEFAULT": TokenBudget(prompt_max=700, response_max=200),
}


@dataclass
class IntermediateFrame:
    request_id: str
    route: str
    intent: str
    entities: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    unknowns: List[str] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    available_evidence_ids: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=lambda: ["answer", "clarify", "abstain"])
    token_budget: TokenBudget = field(default_factory=lambda: ROUTE_TOKEN_BUDGETS["DEFAULT"])
    session_summary: List[str] = field(default_factory=list)
    current_message: str = ""
    notes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class EvidenceItem:
    id: str
    type: str
    source: str
    claims_supported: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class EvidenceBundle:
    items: List[EvidenceItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}


@dataclass
class VerificationReport:
    status: VerificationStatus
    missing_evidence: List[str] = field(default_factory=list)
    claim_results: List[Dict[str, Any]] = field(default_factory=list)
    policy_results: List[Dict[str, Any]] = field(default_factory=list)
    rewrite_needed: bool = False
    fallback_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class ValidationReport:
    status: ValidationStatus
    reasons: List[str] = field(default_factory=list)
    redactions: List[str] = field(default_factory=list)
    final_mode: str = "answer"
    rewritten_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact(asdict(self))
