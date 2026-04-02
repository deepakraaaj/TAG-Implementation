"""Intelligent intent detection service using LLM for query understanding."""
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import get_settings
from app.services.core.llm_retry_service import ainvoke_with_retry
from app.services.core.token_usage_service import TokenUsageService

settings = get_settings()
logger = logging.getLogger(__name__)


class IntentDetectionService:
    """
    AI-powered intent detection that understands user queries and resolves ambiguity.
    
    Solves 99% of cases automatically by:
    - Understanding semantic meaning
    - Inferring missing context
    - Making smart decisions based on domain knowledge
    """

    def __init__(
        self,
        llm: Any,
        domain_provider: Callable[[], Any],
        toon_service: Any,
        manifest_catalog: Any = None,
    ):
        self.llm = llm
        self.domain_provider = domain_provider
        self.toon = toon_service
        self.catalog = manifest_catalog

    @property
    def domain(self):
        return self.domain_provider()

    def _assistant_context(self) -> str:
        cfg = self.domain.get_intent_detection_config()
        context = str(cfg.get("assistant_context", "")).strip()
        if context:
            return context
        description = str(self.domain.description or "").strip()
        return description if description else "domain reporting assistant"

    def _intent_rules(self) -> List[str]:
        cfg = self.domain.get_intent_detection_config()
        rules = [str(item).strip() for item in (cfg.get("rules") or []) if str(item).strip()]
        if rules:
            return rules
        return [
            "Infer operation and target table from the user query and schema aliases.",
            "Treat common status words such as pending/completed/in progress as status filters.",
            "Treat temporal words such as today/yesterday/this week as date filters.",
        ]

    @staticmethod
    def _token_minimization_enabled(metadata: Optional[Dict[str, Any]]) -> bool:
        meta = metadata if isinstance(metadata, dict) else {}
        raw = meta.get("token_minimization")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _build_schema_payload(self, candidate_tables: Dict[str, Any]) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for table_name, table_info in candidate_tables.items():
            if not isinstance(table_info, dict):
                table_info = {}
            payload.append(
                {
                    "table": str(table_name or "").strip(),
                    "description": str(table_info.get("description", "") or "").strip(),
                    "aliases": [str(a).strip() for a in (table_info.get("aliases") or []) if str(a).strip()],
                }
            )
        return payload

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

    def _build_detection_prompt(
        self,
        query: str,
        schema_context: str,
        context_table: str = "",
        recent_conversation: str = "",
        few_shots: List[Dict[str, Any]] = None,
    ) -> str:
        rules_text = "\n".join(f"- {rule}" for rule in self._intent_rules())
        context_hint = ""
        if str(context_table or "").strip():
            context_hint = f'\n**Current Context (Last Table):** "{str(context_table).strip()}"\n'
        recent_hint = ""
        if str(recent_conversation or "").strip():
            recent_hint = f"\n**Recent Conversation Context:**\n{str(recent_conversation).strip()}\n"
        
        # Inject business terms from domain registry if available
        glossary_hint = ""
        glossary = self.domain.spec.language.glossary
        if glossary:
            terms_str = "\n".join([f"- {k}: {v}" for k, v in glossary.items()])
            glossary_hint = f"\n**Business Glossary:**\n{terms_str}\n"
            
        few_shot_hint = ""
        if few_shots:
            shot_lines = []
            for shot in few_shots[:5]:
                shot_lines.append(f"Q: {shot.get('question')}\nA: {json.dumps(shot.get('sql') or shot, indent=2)}")
            few_shot_hint = f"\n**Examples:**\n" + "\n\n".join(shot_lines) + "\n"

        return f"""You are an expert at understanding user intent for a {self._assistant_context()}.
{glossary_hint}
**Available Tables (Ranked by Relevance):**
{schema_context}
{context_hint}
{recent_hint}

**User Query:** "{query}"
{few_shot_hint}
**Task:** Analyze the query and determine:
1. **Operation**: SELECT, INSERT, UPDATE, or DELETE
2. **Target Table**: Which table the user wants to interact with
3. **Filters**: Any filters implied in the query (status, date, etc.)
4. **Confidence**: How confident you are (0-100)

**Important Rules:**
{rules_text}
- If no filters specified, it's okay - return empty filters array

**Response Format (JSON only):**
{{
    "operation": "SELECT|INSERT|UPDATE|DELETE",
    "table": "table_name",
    "filters": [{{"field": "status", "value": "Pending"}}],
    "confidence": 95,
    "reasoning": "Brief explanation of interpretation"
}}

Respond with JSON only, no other text."""

    async def detect_intent(
        self,
        query: str,
        metadata: Dict[str, Any],
        context_table: str = "",
    ) -> Dict[str, Any]:
        intent, _usage = await self.detect_intent_with_usage(query, metadata, context_table=context_table)
        return intent

    async def detect_intent_with_usage(
        self,
        query: str,
        metadata: Optional[Dict[str, Any]] = None,
        context_table: str = "",
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        """
        Detect user intent from natural language query.
        
        Args:
            query: User's natural language query
            metadata: Context (user_id, company_id, etc.)
            
        Returns:
            Intent object with operation, table, filters, confidence
        """
        effective_context_table = str(context_table or "").strip()
        if not effective_context_table and isinstance(metadata, dict):
            effective_context_table = str(metadata.get("pending_select_table", "") or "").strip()
        recent_conversation = self._recent_conversation_text(metadata)
        
        # Day 1 Fix: Use ManifestCatalog to pick relevant tables instead of top 10 truncation
        if self.catalog:
            candidate_tables = self.catalog.get_candidate_tables(query, limit=15)
        else:
            # Fallback to domain manifest truncation if catalog missing
            tables = self.domain.manifest.get("tables", {})
            candidate_tables = dict(list(tables.items())[:10])

        schema_context_plain = self._build_schema_context(candidate_tables)
        schema_context_toon = self.toon.encode(self._build_schema_payload(candidate_tables))
        
        few_shots = self.domain.spec.config.few_shot_examples or []

        prompt_without_toon = self._build_detection_prompt(
            query,
            schema_context_plain,
            context_table=effective_context_table,
            recent_conversation=recent_conversation,
            few_shots=few_shots,
        )
        prompt_with_toon = self._build_detection_prompt(
            query,
            schema_context_toon,
            context_table=effective_context_table,
            recent_conversation=recent_conversation,
            few_shots=few_shots,
        )
        use_toon = self._token_minimization_enabled(metadata)
        prompt = prompt_with_toon if use_toon else prompt_without_toon

        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                task_name="intent_detection",
            )
            usage = TokenUsageService.from_response(
                response,
                prompt_with_toon=prompt,
                prompt_without_toon=prompt_without_toon,
                toon_applied=use_toon,
            )

            # Parse JSON response
            content = str(response.content).strip()
            start, end = content.find("{"), content.rfind("}")
            if start != -1 and end != -1:
                intent = json.loads(content[start:end + 1])
                logger.info(f"Intent detected: {intent}")
                return intent, usage
            
            logger.warning(f"Failed to parse intent response: {content}")
            return self._fallback_intent(query), usage
            
        except Exception as e:
            logger.error(f"Intent detection failed: {e}")
            return self._fallback_intent(query), TokenUsageService.empty()

    def _build_schema_context(self, candidate_tables: Dict[str, Any]) -> str:
        """Build concise schema context for LLM using provided candidate tables."""
        context_lines = []
        for table_name, table_info in candidate_tables.items():
            if not isinstance(table_info, dict):
                table_info = {}
            desc = table_info.get("description", "")
            aliases = table_info.get("aliases", [])
            
            alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
            context_lines.append(f"- **{table_name}**: {desc}{alias_str}")
        
        return "\n".join(context_lines)

    def _fallback_intent(self, query: str) -> Dict[str, Any]:
        """Fallback intent when LLM fails."""
        query_lower = query.lower()
        
        # Simple heuristics
        if any(word in query_lower for word in ["show", "list", "get", "find", "view"]):
            operation = "SELECT"
        elif any(word in query_lower for word in ["create", "add", "new", "insert"]):
            operation = "INSERT"
        elif any(word in query_lower for word in ["update", "change", "modify", "edit"]):
            operation = "UPDATE"
        elif any(word in query_lower for word in ["delete", "remove"]):
            operation = "DELETE"
        else:
            operation = "SELECT"  # Default to SELECT
        
        return {
            "operation": operation,
            "table": "",  # Let table resolution handle it
            "filters": [],
            "confidence": 50,
            "reasoning": "Fallback heuristic"
        }

    def should_ask_clarification(self, intent: Dict[str, Any]) -> bool:
        """
        Determine if we should ask for clarification.
        
        Only ask if confidence is very low (<60%).
        """
        confidence = intent.get("confidence", 0)
        return confidence < 60
