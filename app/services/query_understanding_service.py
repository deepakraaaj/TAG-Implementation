import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.llm_retry_service import ainvoke_with_retry
from app.services.schema_manifest_service import SchemaManifestService
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)
settings = get_settings()


class QueryUnderstandingService:
    def __init__(self, schema_manifest: Optional[SchemaManifestService] = None, llm: Optional[ChatOpenAI] = None):
        self.schema_manifest = schema_manifest or SchemaManifestService()
        self.entity_aliases = self._build_entity_aliases()
        model_name = os.getenv("LLM_MODEL", settings.LLM_MODEL)
        self.llm = llm or ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=model_name,
            temperature=0,
        )

    def _build_entity_aliases(self) -> Dict[str, List[str]]:
        aliases: Dict[str, List[str]] = {}
        tables = self.schema_manifest.manifest.get("tables", {})
        if not isinstance(tables, dict):
            return aliases

        for table_name, meta in tables.items():
            table_aliases = [table_name.lower()]
            custom_aliases = meta.get("aliases", []) if isinstance(meta, dict) else []
            if isinstance(custom_aliases, list):
                table_aliases.extend([str(a).strip().lower() for a in custom_aliases if str(a).strip()])
            if table_name.endswith("s"):
                table_aliases.append(table_name[:-1].lower())
            else:
                table_aliases.append(f"{table_name.lower()}s")
            aliases[table_name] = list(dict.fromkeys([a for a in table_aliases if a]))
        return aliases

    @staticmethod
    def _contains_word(text: str, token: str) -> bool:
        if not text or not token:
            return False
        return bool(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text))

    def _detect_entities(self, query: str) -> List[str]:
        lowered = (query or "").strip().lower()
        if not lowered:
            return []

        matched: List[str] = []
        for entity, aliases in self.entity_aliases.items():
            if any(self._contains_word(lowered, alias) for alias in aliases):
                matched.append(entity)
        return matched

    @staticmethod
    def _detect_intent(query: str) -> str:
        lowered = (query or "").strip().lower()
        if not lowered:
            return "unknown"

        if any(w in lowered for w in ["count", "how many", "total number", "number of"]):
            return "aggregation"
        if any(w in lowered for w in ["create", "add", "new", "insert", "update", "edit", "modify", "delete", "remove"]):
            return "mutation"
        if any(w in lowered for w in ["list", "show", "all ", "fetch", "display"]):
            return "listing"
        if any(w in lowered for w in ["get ", "find ", "details", "detail", "lookup"]):
            return "lookup"
        return "unknown"

    @staticmethod
    def _looks_followup(query: str, messages: Optional[List[Any]]) -> bool:
        lowered = (query or "").strip().lower()
        if not lowered:
            return False

        followup_markers = [
            "it",
            "them",
            "those",
            "that",
            "above",
            "same",
            "these",
            "previous",
            "only active",
        ]
        if any(marker in lowered for marker in followup_markers):
            return True

        if len(lowered.split()) <= 4 and messages and len(messages) >= 2:
            previous_turn = str(messages[-2].content).strip()
            if "?" in previous_turn:
                return True

        return False

    def _entity_catalog(self) -> Dict[str, List[str]]:
        return {k: list(v) for k, v in self.entity_aliases.items()}

    def _build_prompt(self, query: str, messages: Optional[List[Any]]) -> str:
        history = []
        for msg in (messages or [])[-4:]:
            role = "user" if msg.__class__.__name__.lower().startswith("human") else "assistant"
            history.append(f"{role}: {msg.content}")
        history_text = "\n".join(history) or "none"

        return f"""
You classify operational user queries for a multi-turn enterprise assistant.

Return ONLY valid JSON:
{{
  "intent": "listing|aggregation|lookup|mutation|unknown",
  "entities": ["..."],
  "is_self_contained": true,
  "is_followup": false,
  "confidence": 0.0
}}

Rules:
1. Choose entities only from this catalog:
{json.dumps(self._entity_catalog(), ensure_ascii=True)}
2. Mark is_self_contained=true only if latest query can be handled without prior turns.
3. Mark is_followup=true if latest query depends on prior context or unresolved references.
4. confidence must be between 0 and 1.
5. No markdown. No explanation.

Conversation:
{history_text}

Latest query:
{query}
""".strip()

    async def _analyze_with_llm(self, query: str, messages: Optional[List[Any]]) -> Dict[str, Any]:
        prompt = self._build_prompt(query, messages)
        response = await ainvoke_with_retry(
            self.llm,
            prompt,
            max_tokens=120,
            attempts=3,
            backoff_seconds=0.3,
            validator=lambda r: "{" in str(getattr(r, "content", "")) and "}" in str(getattr(r, "content", "")),
            task_name="query_understanding_llm",
        )
        raw = str(response.content).strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM output did not contain JSON object.")

        parsed = json.loads(raw[start:end + 1])
        intent = str(parsed.get("intent", "unknown")).lower()
        if intent not in {"listing", "aggregation", "lookup", "mutation", "unknown"}:
            intent = "unknown"

        entities = parsed.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        allowed_entities = set(self.entity_aliases.keys())
        normalized_entities = [str(e).strip().lower() for e in entities if str(e).strip().lower() in allowed_entities]

        confidence_raw = parsed.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        return {
            "intent": intent,
            "entities": list(dict.fromkeys(normalized_entities)),
            "is_self_contained": bool(parsed.get("is_self_contained", False)),
            "is_followup": bool(parsed.get("is_followup", False)),
            "confidence": confidence,
        }

    def _fallback_analyze(self, query: str, messages: Optional[List[Any]] = None) -> Dict[str, Any]:
        normalized_query = (query or "").strip()
        intent = self._detect_intent(normalized_query)
        entities = self._detect_entities(normalized_query)
        is_followup = self._looks_followup(normalized_query, messages)

        explicit_operational = intent in {"listing", "aggregation", "lookup", "mutation"} and bool(entities)
        entity_only = bool(entities) and len(normalized_query.split()) <= 2
        is_self_contained = explicit_operational or entity_only

        confidence = 0.45
        if explicit_operational:
            confidence = 0.92
        elif entity_only and not is_followup:
            confidence = 0.78
        elif is_followup:
            confidence = 0.75

        return {
            "intent": intent,
            "entities": entities,
            "is_self_contained": is_self_contained,
            "is_followup": is_followup,
            "confidence": confidence,
        }

    async def analyze(self, query: str, messages: Optional[List[Any]] = None) -> Dict[str, Any]:
        try:
            return await self._analyze_with_llm(query, messages)
        except Exception as e:
            logger.warning("Query understanding LLM failed, using fallback rules: %s", e)
            return self._fallback_analyze(query, messages)
