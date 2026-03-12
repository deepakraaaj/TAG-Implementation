import json
import re
from typing import Any, Dict, Optional, Tuple

from app.config import get_settings
from app.services.core.llm_retry_service import ainvoke_with_retry
from app.services.core.token_usage_service import TokenUsageService

settings = get_settings()


class IntentService:
    def __init__(self, llm: Any):
        self.llm = llm

    @staticmethod
    def _parse_simple_kv_pairs(query: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        text = str(query or "")
        if not text:
            return out
        for pattern in (
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,;]+)",
            r"([A-Za-z_][A-Za-z0-9_]*)\s+is\s+([^,;]+)",
        ):
            for key, value in re.findall(pattern, text, flags=re.IGNORECASE):
                out[str(key).strip()] = str(value).strip().strip("'\"")
        return out

    @staticmethod
    def _normalize_update_status(value: str) -> str:
        text = re.sub(r"[_\s]+", " ", str(value or "").strip().lower()).strip()
        aliases = {
            "complete": "Completed",
            "completed": "Completed",
            "done": "Done",
            "open": "Open",
            "pending": "Pending",
            "in progress": "In Progress",
            "overdue": "Overdue",
            "closed": "Closed",
            "cancelled": "Cancelled",
            "canceled": "Canceled",
        }
        if text in aliases:
            return aliases[text]
        return " ".join(part.capitalize() for part in text.split())

    @classmethod
    def _extract_update_fields(cls, query: str) -> Dict[str, Any]:
        text = str(query or "").strip()
        lowered = text.lower()
        fields: Dict[str, Any] = dict(cls._parse_simple_kv_pairs(text))

        if not str(fields.get("id", "")).strip():
            id_patterns = (
                r"\b(?:task|tasks|work\s*item|work\s*items|record)\s*#\s*(\d+)\b",
                r"\b(?:task|tasks|work\s*item|work\s*items|record)\s+id\s*(?:=|:|is)?\s*(\d+)\b",
                r"\bid\s*(?:=|:|is)\s*(\d+)\b",
            )
            for pattern in id_patterns:
                match = re.search(pattern, lowered, flags=re.IGNORECASE)
                if not match:
                    continue
                fields["id"] = str(match.group(1)).strip()
                break

        if not str(fields.get("status", "")).strip():
            candidate = ""
            status_patterns = (
                r"\bstatus\s*(?:to|as|=|:|is)\s*([A-Za-z][A-Za-z _-]{1,40})\b",
                r"\b(?:mark|set|change|update)\b(?:\s+\w+){0,6}?\s+\b(?:to|as)\b\s*([A-Za-z][A-Za-z _-]{1,40})\b",
                r"\b(?:mark|set|change|update)\b(?:\s+\w+){0,6}?\s+(completed|complete|done|pending|open|in progress|overdue|closed|cancelled|canceled)\b",
            )
            for pattern in status_patterns:
                match = re.search(pattern, lowered, flags=re.IGNORECASE)
                if not match:
                    continue
                candidate = str(match.group(1)).strip()
                if candidate:
                    break
            if candidate:
                fields["status"] = cls._normalize_update_status(candidate)

        return fields

    @staticmethod
    def fallback(query: str) -> Dict[str, Any]:
        q = (query or "").lower()
        operation = "select"
        if re.search(r"\b(insert|create|add|new)\b", q):
            operation = "insert"
        elif re.search(r"\b(update|edit|modify|change|set|mark)\b", q):
            operation = "update"

        fields: Dict[str, Any] = {}
        if operation == "update":
            fields = IntentService._extract_update_fields(query)

        return {
            "operation": operation,
            "table": "",
            "filters": {},
            "fields": fields,
        }

    async def analyze(self, query: str, context_table: str = "") -> Dict[str, Any]:
        intent, _usage = await self.analyze_with_usage(query, metadata=None, context_table=context_table)
        return intent

    @staticmethod
    def _token_minimization_enabled(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        raw = meta.get("token_minimization")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _looks_simple_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return True
        if len(q) <= 120:
            if re.search(r"\b(show|list|get|find|select|create|insert|update|change|modify|set|delete|remove)\b", q):
                return True
            if re.search(r"[a-z_][a-z0-9_]*\s*[:=]\s*[^,;]+", q):
                return True
        if len(q) <= 30:
            return True
        return False

    @staticmethod
    def _recent_conversation_text(metadata: Optional[Dict[str, Any]]) -> str:
        meta = metadata if isinstance(metadata, dict) else {}
        explicit = str(meta.get("_recent_conversation_text", "") or "").strip()
        if explicit:
            return explicit
        payload = meta.get("_recent_conversation")
        if not isinstance(payload, list):
            return ""
        lines = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            prefix = "User" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _force_llm(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        raw = meta.get("_intent_force_llm")
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    async def analyze_with_usage(
        self,
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
        context_table: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        effective_context_table = str(context_table or "").strip()
        if not effective_context_table and isinstance(metadata, dict):
            effective_context_table = str(metadata.get("pending_select_table", "") or "").strip()
        context_hint = f"Current Context (Last Table): {effective_context_table}" if effective_context_table else ""
        recent_context = self._recent_conversation_text(metadata)
        recent_context_hint = f"Recent Conversation Context:\n{recent_context}" if recent_context else ""
        fields_hint = ""
        if isinstance(metadata, dict):
            fields_hint = str(metadata.get("_intent_fields_hint", "") or "").strip()
        fields_hint_block = f"Field Extraction Guidance:\n{fields_hint}" if fields_hint else ""
        prompt = f"""
Return ONLY JSON with keys:
operation: select|insert|update
table: db table name or empty string
filters: object
fields: object

{context_hint}
{recent_context_hint}
{fields_hint_block}
User query: {query}
"""
        # If the query is very simple (e.g. "what are they") and has context, 
        # we still want the LLM to resolve it, so we skip the minimization shortcut if it looks like a pronoun query.
        is_pronoun_query = bool(re.search(r"\b(they|them|those|these|it)\b", query.lower()))

        if (
            not self._force_llm(metadata)
            and self._token_minimization_enabled(metadata)
            and self._looks_simple_query(query)
            and not is_pronoun_query
        ):
            return self.fallback(query), TokenUsageService.skipped_call()

        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_intent",
            )
            usage = TokenUsageService.from_response(
                response,
                prompt_with_toon=prompt,
                prompt_without_toon=prompt,
                toon_applied=False,
            )
            raw = str(response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw[start : end + 1])
                parsed.setdefault("operation", "select")
                parsed.setdefault("table", "")
                parsed.setdefault("filters", {})
                parsed.setdefault("fields", {})
                if not isinstance(parsed["filters"], dict):
                    parsed["filters"] = {}
                if not isinstance(parsed["fields"], dict):
                    parsed["fields"] = {}
                return parsed, usage
        except Exception:
            pass

        return self.fallback(query), TokenUsageService.empty()
