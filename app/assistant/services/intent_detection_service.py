"""Intelligent intent detection service using LLM for query understanding."""
import json
import logging
from typing import Dict, Any, Optional, List

from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry
from app.domains.registry import DomainRegistry

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

    def __init__(self):
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            temperature=0.1,  # Low temperature for consistent intent detection
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        self.domain = DomainRegistry.get_current_domain()

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

    async def detect_intent(self, query: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect user intent from natural language query.
        
        Args:
            query: User's natural language query
            metadata: Context (user_id, company_id, etc.)
            
        Returns:
            Intent object with operation, table, filters, confidence
        """
        # Build schema context for LLM
        schema_context = self._build_schema_context()
        
        rules_text = "\n".join(f"- {rule}" for rule in self._intent_rules())
        prompt = f"""You are an expert at understanding user intent for a {self._assistant_context()}.

**Available Tables:**
{schema_context}

**User Query:** "{query}"

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

        try:
            response = await ainvoke_with_retry(
                self.llm,
                prompt,
                attempts=settings.LLM_RETRY_ATTEMPTS,
                backoff_seconds=settings.LLM_RETRY_BACKOFF_SECONDS,
                task_name="intent_detection",
            )
            
            # Parse JSON response
            content = str(response.content).strip()
            start, end = content.find("{"), content.rfind("}")
            if start != -1 and end != -1:
                intent = json.loads(content[start:end + 1])
                logger.info(f"Intent detected: {intent}")
                return intent
            
            logger.warning(f"Failed to parse intent response: {content}")
            return self._fallback_intent(query)
            
        except Exception as e:
            logger.error(f"Intent detection failed: {e}")
            return self._fallback_intent(query)

    def _build_schema_context(self) -> str:
        """Build concise schema context for LLM."""
        manifest = self.domain.manifest
        tables = manifest.get("tables", {})
        
        context_lines = []
        for table_name, table_info in list(tables.items())[:10]:  # Limit to 10 tables
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
