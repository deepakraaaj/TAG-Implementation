from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.guardrails.models import VerificationReport


class VerifierService:
    @staticmethod
    def _bundle_items(evidence: Dict[str, Any] | None) -> List[Dict[str, Any]]:
        payload = evidence.get("items") if isinstance(evidence, dict) else []
        return [dict(item) for item in payload if isinstance(item, dict)]

    @classmethod
    def _has_evidence_type(cls, evidence: Dict[str, Any] | None, evidence_type: str) -> bool:
        return any(str(item.get("type", "") or "").strip() == evidence_type for item in cls._bundle_items(evidence))

    @classmethod
    def _sql_payload(cls, evidence: Dict[str, Any] | None) -> Dict[str, Any]:
        for item in cls._bundle_items(evidence):
            if str(item.get("type", "") or "").strip() != "sql_rowset":
                continue
            payload = item.get("payload")
            return dict(payload) if isinstance(payload, dict) else {}
        return {}

    @staticmethod
    def _extract_total_count(rows: List[Dict[str, Any]]) -> int | None:
        if not rows:
            return None
        first = dict(rows[0] or {})
        for key in ("total_tasks", "_total_count", "total_count", "count"):
            value = first.get(key)
            try:
                parsed = int(value)
                if parsed >= 0:
                    return parsed
            except Exception:
                continue
        return None

    @staticmethod
    def _operation(sql: str) -> str:
        text = str(sql or "").strip()
        if not text:
            return ""
        return text.split(None, 1)[0].strip().lower()

    @staticmethod
    def _extract_numbers(text: str) -> List[str]:
        return re.findall(r"\b\d+\b", str(text or ""))

    @classmethod
    def _contains_causal_claim(cls, text: str) -> bool:
        return bool(re.search(r"\b(because|due to|caused by|reason)\b", str(text or "").lower()))

    @classmethod
    def _looks_like_unverified_data_claim(cls, candidate: str, frame: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
        if cls._has_evidence_type(evidence, "sql_rowset"):
            return False
        candidate_text = str(candidate or "").strip()
        if not candidate_text:
            return False
        lower = candidate_text.lower()
        if not re.search(r"\b(you have|there are|i found|count|status|scheduled|records|tasks|assets|facilities)\b", lower):
            return False
        candidate_numbers = set(cls._extract_numbers(candidate_text))
        query_numbers = set(cls._extract_numbers(str(frame.get("current_message", "") or "")))
        return bool(candidate_numbers - query_numbers) or "status is" in lower or "there are" in lower

    @staticmethod
    def _clarify_message(frame: Dict[str, Any]) -> str:
        unknowns = [str(item or "").strip() for item in (frame.get("unknowns") or []) if str(item or "").strip()]
        if "referent" in unknowns:
            return "What does 'it' refer to in your request?"
        if "target_entity" in unknowns:
            return "Which entity do you want me to look at?"
        return "Can you clarify the missing detail?"

    @staticmethod
    def _abstain_message(frame: Dict[str, Any]) -> str:
        question_type = str(((frame.get("notes") or {}).get("question_type")) or "").strip().lower()
        if question_type == "causal":
            return "I do not have explicit evidence for the cause. I can help you check the related status or records instead."
        if bool(((frame.get("notes") or {}).get("requires_data_evidence"))):
            return "I do not have enough validated data to answer that directly. Ask me to list or count the relevant records."
        return "I do not have enough validated information to answer that safely."

    @classmethod
    def _supported_sql_message(cls, sql_payload: Dict[str, Any]) -> str:
        sql = str(sql_payload.get("sql", "") or "").strip()
        operation = cls._operation(sql)
        row_count = int(sql_payload.get("row_count") or 0)
        rows = sql_payload.get("rows")
        rows_preview = rows if isinstance(rows, list) else []
        shown_count = int(sql_payload.get("shown_count") or len(rows_preview))
        total_records = sql_payload.get("total_records")
        if total_records is None:
            total_records = cls._extract_total_count(rows_preview)
        try:
            total_count = int(total_records) if total_records is not None else None
        except Exception:
            total_count = None

        if operation == "insert":
            return f"Insert successful. Rows affected: {row_count}."
        if operation == "update":
            return f"Update successful. Rows affected: {row_count}."
        if row_count <= 0:
            return "No records found for the selected filters."
        if "count(" in sql.lower() and total_count is not None:
            return f"Count: {total_count}."
        if total_count is not None:
            if shown_count > 0 and shown_count < total_count:
                return f"Total {total_count} records found. Showing {shown_count}."
            return f"Total {total_count} records found."
        return f"Found {row_count} record(s)."

    def _verify_chat(self, candidate: str, frame: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
        unknowns = [str(item or "").strip() for item in (frame.get("unknowns") or []) if str(item or "").strip()]
        if unknowns:
            return VerificationReport(
                status="clarify",
                missing_evidence=unknowns,
                policy_results=[{"check": "unknowns_resolved", "passed": False}],
                fallback_message=self._clarify_message(frame),
            ).to_dict()

        if self._contains_causal_claim(candidate) and "explicit_cause" in (frame.get("required_evidence") or []):
            return VerificationReport(
                status="abstain",
                missing_evidence=["explicit_cause"],
                policy_results=[{"check": "causal_claim_requires_explicit_evidence", "passed": False}],
                fallback_message=self._abstain_message(frame),
            ).to_dict()

        if bool(((frame.get("notes") or {}).get("requires_data_evidence"))) and not self._has_evidence_type(evidence, "sql_rowset"):
            return VerificationReport(
                status="abstain",
                missing_evidence=["sql_rowset"],
                policy_results=[{"check": "data_claim_requires_sql_evidence", "passed": False}],
                fallback_message=self._abstain_message(frame),
            ).to_dict()

        if self._looks_like_unverified_data_claim(candidate, frame, evidence):
            return VerificationReport(
                status="abstain",
                missing_evidence=["sql_rowset"],
                policy_results=[{"check": "candidate_adds_unverified_data_claim", "passed": False}],
                fallback_message=self._abstain_message(frame),
            ).to_dict()

        return VerificationReport(
            status="pass",
            claim_results=[{"claim": "candidate_accepted", "supported": True}],
            policy_results=[{"check": "chat_guardrails", "passed": True}],
        ).to_dict()

    def _verify_sql(self, candidate: str, frame: Dict[str, Any], evidence: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        if str(state.get("error", "") or "").strip():
            return VerificationReport(
                status="pass",
                claim_results=[{"claim": "error_response", "supported": True}],
                policy_results=[{"check": "error_path", "passed": True}],
            ).to_dict()

        sql = str(state.get("sql_query", "") or "").strip()
        if not sql or sql.upper() == "SKIP":
            return VerificationReport(
                status="pass",
                claim_results=[{"claim": "clarification_or_skip_message", "supported": True}],
                policy_results=[{"check": "skip_path", "passed": True}],
            ).to_dict()

        if self._contains_causal_claim(candidate):
            return VerificationReport(
                status="abstain",
                missing_evidence=["explicit_cause"],
                policy_results=[{"check": "causal_claim_requires_explicit_evidence", "passed": False}],
                fallback_message=self._abstain_message(frame),
            ).to_dict()

        sql_payload = self._sql_payload(evidence)
        if not sql_payload:
            return VerificationReport(
                status="abstain",
                missing_evidence=["sql_rowset"],
                policy_results=[{"check": "sql_evidence_available", "passed": False}],
                fallback_message=self._abstain_message(frame),
            ).to_dict()

        operation = self._operation(sql)
        row_count = int(sql_payload.get("row_count") or 0)
        shown_count = int(sql_payload.get("shown_count") or 0)
        total_records = sql_payload.get("total_records")
        rows = sql_payload.get("rows") if isinstance(sql_payload.get("rows"), list) else []
        if total_records is None:
            total_records = self._extract_total_count(rows)
        try:
            total_count = int(total_records) if total_records is not None else None
        except Exception:
            total_count = None

        candidate_text = str(candidate or "").strip()
        update_match = re.fullmatch(r"Update successful\. Rows affected: (\d+)\.", candidate_text)
        insert_match = re.fullmatch(r"Insert successful\. Rows affected: (\d+)\.", candidate_text)
        count_match = re.fullmatch(r"Count: (\d+)\.", candidate_text)
        total_shown_match = re.fullmatch(r"Total (\d+) .+ found\. Showing (\d+)\.", candidate_text)
        total_only_match = re.fullmatch(r"Total (\d+) .+ found\.", candidate_text)
        found_match = re.fullmatch(r"Found (\d+) record\(s\)\.", candidate_text)

        supported = True
        if operation == "update" and update_match:
            supported = int(update_match.group(1)) == row_count
        elif operation == "insert" and insert_match:
            supported = int(insert_match.group(1)) == row_count
        elif count_match and total_count is not None:
            supported = int(count_match.group(1)) == total_count
        elif total_shown_match and total_count is not None:
            supported = int(total_shown_match.group(1)) == total_count and int(total_shown_match.group(2)) == shown_count
        elif total_only_match and total_count is not None:
            supported = int(total_only_match.group(1)) == total_count
        elif found_match:
            supported = int(found_match.group(1)) == row_count
        elif row_count <= 0 and "no records" in candidate_text.lower():
            supported = True

        if not supported:
            return VerificationReport(
                status="pass",
                claim_results=[{"claim": "sql_response_numbers", "supported": False}],
                policy_results=[{"check": "sql_claims_match_evidence", "passed": False}],
                rewrite_needed=True,
                fallback_message=self._supported_sql_message(sql_payload),
            ).to_dict()

        return VerificationReport(
            status="pass",
            claim_results=[{"claim": "sql_response_supported", "supported": True}],
            policy_results=[{"check": "sql_claims_match_evidence", "passed": True}],
        ).to_dict()

    def verify(self, candidate: str, frame: Dict[str, Any], evidence: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        route = str(frame.get("route", "") or state.get("route", "") or "CHAT").strip().upper() or "CHAT"
        if route == "SQL":
            return self._verify_sql(candidate, frame, evidence, state)
        return self._verify_chat(candidate, frame, evidence)
