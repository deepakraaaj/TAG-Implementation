from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Dict, List

from app.services.guardrails.models import ROUTE_TOKEN_BUDGETS, IntermediateFrame, TokenBudget


class IntermediateService:
    def __init__(self, domain_provider: Callable[[], Any] | None = None):
        self.domain_provider = domain_provider

    @staticmethod
    def _recent_summary(metadata: Dict[str, Any] | None) -> List[str]:
        meta = metadata if isinstance(metadata, dict) else {}
        payload = meta.get("_recent_conversation")
        if not isinstance(payload, list):
            return []
        lines: List[str] = []
        for item in payload[-5:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            prefix = "User" if role == "user" else "Assistant"
            compact = re.sub(r"\s+", " ", content)
            if len(compact) > 120:
                compact = compact[:117].rstrip() + "..."
            lines.append(f"{prefix}: {compact}")
        return lines

    @staticmethod
    def _question_type(message: str) -> str:
        text = str(message or "").strip().lower()
        if not text:
            return "general"
        if re.search(r"\b(what can you do|help|capabilities|examples|features)\b", text):
            return "help"
        if re.search(r"\b(why|reason|cause|caused|because)\b", text):
            return "causal"
        if re.search(r"\b(how many|count|total)\b", text):
            return "count"
        if re.search(r"\b(status|show|list|find|which|who|when|open|pending|completed|overdue)\b", text):
            return "lookup"
        return "general"

    @staticmethod
    def _is_referential_followup(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        return bool(re.search(r"\b(it|they|them|those|these|that|this)\b", text))

    @staticmethod
    def _infer_entities(message: str, intent: Dict[str, Any] | None) -> List[str]:
        entities: List[str] = []
        parsed_intent = intent if isinstance(intent, dict) else {}
        table = str(parsed_intent.get("table", "") or "").strip()
        if table:
            entities.append(table)
        lowered = str(message or "").strip().lower()
        for label in ("tasks", "task", "assets", "asset", "facilities", "facility", "users", "user", "reports", "report"):
            if re.search(rf"\b{re.escape(label)}\b", lowered):
                entities.append(label)
        normalized: List[str] = []
        for item in entities:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @staticmethod
    def _filters(intent: Dict[str, Any] | None) -> Dict[str, Any]:
        parsed_intent = intent if isinstance(intent, dict) else {}
        filters = parsed_intent.get("filters")
        return dict(filters) if isinstance(filters, dict) else {}

    @staticmethod
    def _intent_label(route: str, intent: Dict[str, Any] | None, question_type: str) -> str:
        parsed_intent = intent if isinstance(intent, dict) else {}
        operation = str(parsed_intent.get("operation", "") or "").strip().lower()
        table = str(parsed_intent.get("table", "") or "").strip()
        if operation and table:
            return f"{operation}:{table}"
        if operation:
            return operation
        if route == "CHAT":
            return question_type
        return "unknown"

    @staticmethod
    def _token_budget(route: str, metadata: Dict[str, Any] | None, question_type: str) -> TokenBudget:
        meta = metadata if isinstance(metadata, dict) else {}
        compact = bool(meta.get("token_minimization", True))
        
        base_budget = ROUTE_TOKEN_BUDGETS.get(route, ROUTE_TOKEN_BUDGETS["DEFAULT"])
        prompt_max = base_budget.prompt_max
        response_max = base_budget.response_max

        if not compact and route != "SQL":
            prompt_max = int(prompt_max * 1.5)

        if question_type == "help":
            response_max = max(response_max, 250)
        elif question_type == "causal":
            response_max = min(response_max, 100)
            
        return TokenBudget(prompt_max=prompt_max, response_max=response_max)

    @staticmethod
    def _required_evidence(route: str, question_type: str, entities: List[str], state: Dict[str, Any]) -> List[str]:
        required: List[str] = []
        sql_query = str(state.get("sql_query", "") or "").strip().upper()
        error = str(state.get("error", "") or "").strip()
        if route == "SQL" and not error and sql_query != "SKIP":
            required.append("sql_rowset")
        elif route == "CHAT" and question_type == "help":
            required.append("domain_config")
        elif route == "CHAT" and question_type in {"count", "lookup"} and entities:
            required.append("sql_rowset")
        if question_type == "causal":
            required.append("explicit_cause")
        return list(dict.fromkeys(required))

    def build(self, state: Dict[str, Any]) -> Dict[str, Any]:
        messages = state.get("messages") or []
        message = str(messages[-1].content) if messages else ""
        metadata = state.get("metadata") or {}
        route = str(state.get("route", "") or "CHAT").strip().upper() or "CHAT"
        intent = state.get("intent") if isinstance(state.get("intent"), dict) else {}
        question_type = self._question_type(message)
        session_summary = self._recent_summary(metadata)
        entities = self._infer_entities(message, intent)
        filters = self._filters(intent)
        referential = self._is_referential_followup(message)
        unknowns: List[str] = []
        if referential and not session_summary:
            unknowns.append("referent")
        if route == "SQL" and question_type in {"count", "lookup", "causal"} and not entities:
            unknowns.append("target_entity")

        allowed_actions = ["answer", "clarify", "abstain"]
        if route == "SQL":
            allowed_actions.append("reject")

        request_id = str(
            metadata.get("trace_id")
            or metadata.get("request_id")
            or metadata.get("session_id")
            or state.get("request_id")
            or uuid.uuid4()
        ).strip()

        frame = IntermediateFrame(
            request_id=request_id,
            route=route,
            intent=self._intent_label(route, intent, question_type),
            entities=entities,
            filters=filters,
            unknowns=unknowns,
            required_evidence=self._required_evidence(route, question_type, entities, state),
            allowed_actions=allowed_actions,
            token_budget=self._token_budget(route, metadata, question_type),
            session_summary=session_summary,
            current_message=message,
            notes={
                "question_type": question_type,
                "referential_followup": referential,
                "requires_data_evidence": "sql_rowset" in self._required_evidence(route, question_type, entities, state),
            },
        )
        return frame.to_dict()
