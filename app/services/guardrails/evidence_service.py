from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.services.guardrails.models import EvidenceBundle, EvidenceItem


class EvidenceService:
    def __init__(self, domain_provider: Callable[[], Any] | None = None):
        self.domain_provider = domain_provider

    @staticmethod
    def _normalized_rows(rows: Any, limit: int = 5) -> List[Dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        normalized: List[Dict[str, Any]] = []
        for item in rows[:limit]:
            if isinstance(item, dict):
                normalized.append(dict(item))
        return normalized

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
    def _sql_claims(sql: str, row_count: int, total_records: int | None, rows: List[Dict[str, Any]]) -> List[str]:
        claims = [f"row_count={max(0, int(row_count or 0))}"]
        operation = str(sql or "").strip().split(None, 1)[0].lower() if str(sql or "").strip() else ""
        if operation:
            claims.append(f"operation={operation}")
        claims.append(f"rows_shown={len(rows)}")
        if total_records is not None:
            claims.append(f"total_records={int(total_records)}")
        for index, row in enumerate(rows[:3], start=1):
            for key, value in list(dict(row or {}).items())[:6]:
                claims.append(f"row[{index}].{key}={value}")
        return claims

    @staticmethod
    def _user_context_payload(metadata: Dict[str, Any]) -> Dict[str, Any]:
        company = metadata.get("company")
        company_name = ""
        if isinstance(company, dict):
            company_name = str(company.get("name", "") or "").strip()
        payload = {
            "user_id": str(metadata.get("user_id") or metadata.get("userId") or "").strip(),
            "user_role": str(metadata.get("user_role") or metadata.get("userRole") or metadata.get("role") or "").strip(),
            "user_name": str(metadata.get("user_name") or "").strip(),
            "company_name": str(metadata.get("company_name") or metadata.get("companyName") or company_name or "").strip(),
        }
        return {key: value for key, value in payload.items() if value}

    def _domain_item(self) -> EvidenceItem | None:
        if not callable(self.domain_provider):
            return None
        try:
            domain = self.domain_provider()
        except Exception:
            return None
        if domain is None:
            return None
        config = getattr(domain, "config", {}) or {}
        description = str(getattr(domain, "description", "") or "").strip()
        capabilities_getter = getattr(domain, "get_capabilities", None)
        capabilities = capabilities_getter() if callable(capabilities_getter) else {}
        examples = capabilities.get("examples") if isinstance(capabilities, dict) else []
        payload = {
            "bot_name": str(config.get("bot_name", "") or "").strip(),
            "description": description,
            "examples": [str(item or "").strip() for item in (examples or []) if str(item or "").strip()][:5],
        }
        claims = [f"bot_name={payload['bot_name']}"] if payload.get("bot_name") else []
        claims.extend([f"example={item}" for item in payload.get("examples", [])[:3]])
        return EvidenceItem(
            id="domain.config",
            type="domain_config",
            source="domain_registry",
            claims_supported=claims,
            payload=payload,
        )

    def assemble(self, state: Dict[str, Any], frame: Dict[str, Any] | None = None) -> Dict[str, Any]:
        bundle = EvidenceBundle()
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        rows = self._normalized_rows(state.get("rows_preview"))
        sql = str(state.get("sql_query", "") or "").strip()
        row_count = int(state.get("row_count") or 0)
        total_records = state.get("total_records")
        if total_records is None:
            total_records = self._extract_total_count(rows)
        try:
            normalized_total = int(total_records) if total_records is not None else None
        except Exception:
            normalized_total = None

        if sql and sql.upper() != "SKIP":
            bundle.items.append(
                EvidenceItem(
                    id="sql.execution",
                    type="sql_rowset",
                    source="sql_execute",
                    claims_supported=self._sql_claims(sql, row_count, normalized_total, rows),
                    payload={
                        "sql": sql,
                        "row_count": row_count,
                        "shown_count": len(rows),
                        "total_records": normalized_total,
                        "rows": rows,
                    },
                )
            )

        session_summary = []
        if isinstance(frame, dict):
            payload = frame.get("session_summary")
            if isinstance(payload, list):
                session_summary = [str(item or "").strip() for item in payload if str(item or "").strip()]
        if session_summary:
            bundle.items.append(
                EvidenceItem(
                    id="runtime.session_summary",
                    type="runtime_state",
                    source="chat_service",
                    claims_supported=[f"turn={item}" for item in session_summary[:5]],
                    payload={"summary": session_summary[:5]},
                )
            )

        user_context = self._user_context_payload(metadata)
        if user_context:
            bundle.items.append(
                EvidenceItem(
                    id="user.context",
                    type="user_context",
                    source="request_metadata",
                    claims_supported=[f"{key}={value}" for key, value in user_context.items()],
                    payload=user_context,
                )
            )

        domain_item = self._domain_item()
        if domain_item is not None:
            bundle.items.append(domain_item)

        if str(state.get("error", "") or "").strip():
            bundle.items.append(
                EvidenceItem(
                    id="runtime.error",
                    type="runtime_state",
                    source="workflow_state",
                    claims_supported=["error_present"],
                    payload={"error": str(state.get("error") or "").strip()},
                )
            )

        return bundle.to_dict()
