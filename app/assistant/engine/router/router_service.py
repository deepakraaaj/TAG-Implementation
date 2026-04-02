import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog
from app.config import get_settings
from app.services.core.llm_retry_service import ainvoke_with_retry
from app.services.core.token_usage_service import TokenUsageService

logger = logging.getLogger(__name__)
settings = get_settings()


class RouterService:
    def __init__(
        self,
        llm: Any,
        manifest_catalog: ManifestCatalog,
        domain_provider: Callable[[], Any],
    ):
        self.llm = llm
        self.manifest_catalog = manifest_catalog
        self.domain_provider = domain_provider
        self._cached_sql_terms: dict[str, Set[str]] = {}
        self._cached_report_terms: dict[str, Set[str]] = {}

    def _domain_cache_key(self) -> str:
        try:
            domain = self.domain_provider() if callable(self.domain_provider) else None
        except Exception:
            domain = None
        return str(getattr(domain, "name", "") or getattr(domain, "domain_name", "") or "default").strip() or "default"

    @staticmethod
    def _default_sql_terms() -> Set[str]:
        return {
            "select",
            "insert",
            "update",
            "create",
            "add",
            "edit",
            "modify",
            "show",
            "list",
            "count",
            "get",
            "find",
            "delete",
            "remove",
            "asset",
            "assets",
            "task",
            "tasks",
            "user",
            "users",
            "employee",
            "employees",
            "facility",
            "facilities",
            "work order",
            "work orders",
            "assigned",
        }

    @staticmethod
    def _default_report_terms() -> Set[str]:
        return {
            "report",
            "reports",
            "report list",
            "available reports",
        }

    def _sql_terms(self) -> Set[str]:
        cache_key = self._domain_cache_key()
        cache_store = getattr(self, "_cached_sql_terms", None)
        if not isinstance(cache_store, dict):
            cache_store = {}
            self._cached_sql_terms = cache_store
        cached_terms = cache_store.get(cache_key)
        if isinstance(cached_terms, set) and cached_terms:
            return cached_terms

        # Keep core operation verbs generic and provider/domain agnostic.
        terms: Set[str] = set(self._default_sql_terms())

        try:
            manifest_catalog = getattr(self, "manifest_catalog", None)
            for table_name in manifest_catalog.table_names():
                terms.add(str(table_name or "").strip().lower())
                for alias in manifest_catalog.aliases(table_name):
                    a = str(alias or "").strip().lower()
                    if a:
                        terms.add(a)
        except Exception:
            pass

        try:
            domain_provider = getattr(self, "domain_provider", None)
            domain = domain_provider() if callable(domain_provider) else None
            capabilities = domain.get_capabilities() if hasattr(domain, "get_capabilities") else {}
            tables_description = capabilities.get("tables_description") if isinstance(capabilities, dict) else {}
            if isinstance(tables_description, dict):
                for table_name in tables_description.keys():
                    name = str(table_name or "").strip().lower()
                    if name:
                        terms.add(name)
        except Exception:
            pass

        resolved_terms = {t for t in terms if t}
        cache_store[cache_key] = resolved_terms
        return resolved_terms

    @staticmethod
    def _looks_like_report_query(query: str, report_terms: Optional[Set[str]] = None) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return False

        terms = {str(term or "").strip().lower() for term in (report_terms or RouterService._default_report_terms()) if str(term or "").strip()}
        if q in terms:
            return True

        patterns = [
            r"\b(list|show|available|what|which)\s+reports?\b",
            r"\breport\s+list\b",
            r"\b(run|generate|open|view|get|show)\b.*\breports?\b",
        ]
        if any(re.search(pattern, q) for pattern in patterns):
            return True

        # Guard: report-name aliases (for example: "pending tasks") should not
        # force REPORT routing unless user explicitly asks for a report.
        if not re.search(r"\breports?\b", q):
            return False

        terms = terms or RouterService._default_report_terms()
        for term in terms:
            if " " in term:
                if term in q:
                    return True
                continue
            if re.search(rf"\b{re.escape(term)}\b", q):
                return True
        return False

    def _report_terms(self) -> Set[str]:
        cache_key = self._domain_cache_key()
        cache_store = getattr(self, "_cached_report_terms", None)
        if not isinstance(cache_store, dict):
            cache_store = {}
            self._cached_report_terms = cache_store
        cached_terms = cache_store.get(cache_key)
        if isinstance(cached_terms, set) and cached_terms:
            return cached_terms

        terms: Set[str] = set(self._default_report_terms())
        try:
            domain_provider = getattr(self, "domain_provider", None)
            domain = domain_provider() if callable(domain_provider) else None
            domain_path = Path(getattr(domain, "domain_path", "") or "")
            reports_file = domain_path / "reports.json"
            if reports_file.exists():
                payload = json.loads(reports_file.read_text())
                reports = payload.get("reports") if isinstance(payload, dict) else {}
                if isinstance(reports, dict):
                    for report_id, report_config in reports.items():
                        report_name = ""
                        report_aliases: list[str] = []
                        if isinstance(report_config, dict):
                            report_name = str(report_config.get("name", "")).strip().lower()
                            aliases = report_config.get("aliases")
                            if isinstance(aliases, list):
                                report_aliases = [str(alias or "").strip().lower() for alias in aliases if str(alias or "").strip()]
                        normalized_id = str(report_id or "").strip().replace("_", " ").lower()
                        if normalized_id:
                            terms.add(normalized_id)
                        if report_name:
                            terms.add(report_name)
                        terms.update({alias for alias in report_aliases if alias})
        except Exception:
            pass

        resolved_terms = {t for t in terms if t}
        cache_store[cache_key] = resolved_terms
        return resolved_terms

    @staticmethod
    def fallback(
        query: str,
        sql_terms: Optional[Set[str]] = None,
        report_terms: Optional[Set[str]] = None,
    ) -> str:
        """Fallback heuristic for routing when LLM fails."""
        q = (query or "").strip().lower()

        if RouterService._looks_like_report_query(q, report_terms=report_terms):
            return "REPORT"

        terms = sql_terms or RouterService._default_sql_terms()
        for term in terms:
            if " " in term:
                if term in q:
                    return "SQL"
                continue
            if re.search(rf"\b{re.escape(term)}\b", q):
                return "SQL"
        return "CHAT"

    @staticmethod
    def _is_clear_chat_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return True

        greeting_patterns = [
            r"^(hi|hello|hey)\b",
            r"^good\s+(morning|afternoon|evening)\b",
            r"^(how are you|what's up|whats up)\b",
            r"^(thanks|thank you)\b",
            r"^(ok|okay|cool|nice)\b",
        ]
        if any(re.search(pattern, q) for pattern in greeting_patterns):
            return True

        # Short conversational prompts are high-confidence CHAT.
        if len(q) <= 24 and re.fullmatch(r"[a-zA-Z\s!?.,']+", q):
            return True
        return False

    @staticmethod
    def _looks_like_sql_lookup_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return False

        conceptual_patterns = [
            r"\bwho are you\b",
            r"\bwhat can you do\b",
            r"\bhelp\b",
            r"\bcapabilities\b",
            r"\bfeatures\b",
            r"\bwhat is\b",
            r"\bwhat are\b",
            r"\bexplain\b",
            r"\bdefine\b",
            r"\bmeaning of\b",
        ]
        if any(re.search(pattern, q) for pattern in conceptual_patterns):
            return False

        lookup_patterns = [
            r"^\s*(show|list|get|find|view|count)\b",
            r"\b(how many|total|status|pending|completed|complete|done|open|closed|overdue)\b",
            r"\b(today|yesterday|this week|last week)\b",
            r"\b(for|assigned to|where)\b",
            r"\b(mapped?|mapping|linked|associated)\b",
            r"[=:]",
        ]
        return any(re.search(pattern, q) for pattern in lookup_patterns)

    @staticmethod
    def _is_referential_followup(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return False
        patterns = [
            r"\b(what are they|what are those|show them|list them)\b",
            r"\b(they|them|those|these|it|that)\b",
        ]
        return any(re.search(pattern, q) for pattern in patterns)

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
    def _has_pending_select_context(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        pending_table = str(meta.get("pending_select_table", "") or "").strip()
        pending_negation = meta.get("pending_select_negation")
        return bool(pending_table) or isinstance(pending_negation, dict)

    @classmethod
    def _coerce_route_for_context(
        cls,
        route: str,
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
        fallback_route: str = "",
    ) -> str:
        normalized_route = str(route or "").strip().upper()
        if normalized_route not in {"SQL", "CHAT", "REPORT"}:
            return normalized_route

        # Referential follow-up should stay in SQL lane if we have pending select context.
        if (
            normalized_route in {"CHAT", "REPORT"}
            and cls._is_referential_followup(query)
            and cls._has_pending_select_context(metadata)
        ):
            return "SQL"

        # Guard against over-eager LLM REPORT classifications for plain data queries.
        # Keep REPORT only when heuristic fallback also sees report intent.
        normalized_fallback = str(fallback_route or "").strip().upper()
        if normalized_route == "REPORT" and normalized_fallback in {"SQL", "CHAT"}:
            return normalized_fallback

        # Respect high-confidence heuristic report matches such as exact aliases
        # configured in reports.json.
        if normalized_route == "CHAT" and normalized_fallback == "REPORT":
            return "REPORT"

        # Guard against under-classified CHAT predictions for obvious data lookups.
        if (
            normalized_route == "CHAT"
            and normalized_fallback == "SQL"
            and cls._looks_like_sql_lookup_query(query)
        ):
            return "SQL"
        return normalized_route

    async def route(self, query: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        route, _usage = await self.route_with_usage(query, metadata=metadata)
        return route

    async def route_with_usage(
        self,
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, int]]:
        """Route query to appropriate handler.

        Initial routing is LLM-first. Heuristic fallback is used only if the
        model call fails or returnWhich users are mapped to which locations?s invalid output.
        """
        q = str(query or "").strip()
        if not q:
            return "CHAT", TokenUsageService.skipped_call()

        meta = metadata if isinstance(metadata, dict) else {}
        recent_conversation = self._recent_conversation_text(meta)
        fallback_route = self.fallback(
            q,
            sql_terms=self._sql_terms(),
            report_terms=self._report_terms(),
        )
        context_block = ""
        if recent_conversation:
            context_block = f"\nRecent Conversation (last 5 turns):\n{recent_conversation}\n"

        prompt = f"""
Classify user message as SQL, CHAT, or REPORT.
Return only JSON: {{"route":"SQL|CHAT|REPORT"}}
If the current query is referential (for example "what are they"), use recent conversation to resolve route.
{context_block}
User: {q}
"""
        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                validator=lambda r: "{" in str(getattr(r, "content", "")),
                task_name="v2_router",
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
                route = str(parsed.get("route", "")).upper()
                if route in {"SQL", "CHAT", "REPORT"}:
                    coerced = self._coerce_route_for_context(route, q, meta, fallback_route=fallback_route)
                    return coerced, usage
        except Exception as exc:
            logger.warning("Router LLM classification failed, using fallback route: %s", exc)

        fallback_route = self.fallback(
            q,
            sql_terms=self._sql_terms(),
            report_terms=self._report_terms(),
        )
        coerced_fallback = self._coerce_route_for_context(
            fallback_route,
            q,
            meta,
            fallback_route=fallback_route,
        )
        return coerced_fallback, TokenUsageService.empty()
