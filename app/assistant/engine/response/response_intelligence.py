"""Intelligent response system for request validation and capability discovery."""
import logging
import re
from typing import Any, Callable, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of request validation."""
    is_valid: bool
    reason: str = ""
    suggestion: str = ""


class ResponseIntelligence:
    """
    Intelligent response system that validates requests and provides helpful guidance.
    
    Features:
    - Request feasibility validation
    - Off-topic detection
    - Capability discovery
    - Graceful error handling
    """

    def __init__(self, domain_provider: Callable[[], Any], llm: Any):
        """Initialize with domain registry."""
        self.domain = domain_provider()
        self.llm = llm

    @staticmethod
    def _humanize_label(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()

    def _domain_knowledge(self) -> Dict[str, Any]:
        getter = getattr(self.domain, "get_domain_knowledge_config", None)
        payload = getter() if callable(getter) else {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _behavior_summary(self) -> str:
        knowledge = self._domain_knowledge()
        reasoning = knowledge.get("reasoning_profile") if isinstance(knowledge.get("reasoning_profile"), dict) else {}
        summary = str(reasoning.get("behavior_summary", "") or "").strip()
        if summary:
            return summary
        return "Direct answer first, one clarification if needed, and abstain instead of guessing when validated evidence is missing."

    def _example_queries(self) -> List[str]:
        knowledge = self._domain_knowledge()
        examples = knowledge.get("example_queries") if isinstance(knowledge, dict) else []
        normalized = [str(item or "").strip() for item in (examples or []) if str(item or "").strip()]
        if normalized:
            return normalized
        capabilities = self.domain.get_capabilities()
        fallback = capabilities.get("examples", []) if isinstance(capabilities, dict) else []
        return [str(item or "").strip() for item in (fallback or []) if str(item or "").strip()]

    def _workflow_labels(self) -> List[str]:
        knowledge = self._domain_knowledge()
        workflows = knowledge.get("workflows") if isinstance(knowledge, dict) else []
        labels: List[str] = []
        if isinstance(workflows, list):
            for item in workflows:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "") or "").strip()
                if label:
                    labels.append(label)
        return labels[:4]

    def _domain_topics(self) -> List[str]:
        topics: List[str] = []
        knowledge = self._domain_knowledge()
        primary_entities = knowledge.get("primary_entities") if isinstance(knowledge, dict) else []
        for item in primary_entities or []:
            cleaned = self._humanize_label(str(item or ""))
            if cleaned:
                topics.append(cleaned)

        entity_behavior_getter = getattr(self.domain, "get_entity_behavior_config", None)
        entity_behavior = entity_behavior_getter() if callable(entity_behavior_getter) else {}
        primary_label = str(entity_behavior.get("primary_label", "") or "").strip()
        if primary_label:
            topics.append(self._humanize_label(primary_label))

        capabilities = self.domain.get_capabilities()
        tables_desc = capabilities.get("tables_description", {}) if isinstance(capabilities, dict) else {}
        if isinstance(tables_desc, dict):
            for table_name in tables_desc.keys():
                cleaned = self._humanize_label(str(table_name or ""))
                if cleaned:
                    topics.append(cleaned)

        normalized: List[str] = []
        seen: set[str] = set()
        for item in topics:
            lowered = str(item or "").strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(item)
        return normalized[:5]

    def domain_scope(self) -> str:
        knowledge = self._domain_knowledge()
        scope = str(knowledge.get("scope", "") if isinstance(knowledge, dict) else "").strip()
        if scope:
            return scope

        capabilities = self.domain.get_capabilities()
        description = str(capabilities.get("description", "") if isinstance(capabilities, dict) else "").strip()
        if description:
            cleaned = re.sub(r"^i\s+help\s+you\s+(?:query\s+and\s+manage|manage|with)\s+", "", description, flags=re.IGNORECASE)
            cleaned = re.sub(r"^i\s+answer\s+", "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip(" .")
            if cleaned:
                return cleaned

        topics = self._domain_topics()
        if topics:
            return ", ".join(topics)

        domain_name = str(getattr(self.domain, "name", "") or "").strip()
        return self._humanize_label(domain_name) or "the configured business domain"

    def validate_request(self, message: str, intent: Dict[str, Any]) -> ValidationResult:
        """
        Validate if a request is feasible.
        
        Args:
            message: User's message
            intent: Parsed intent with table, operation, etc.
            
        Returns:
            ValidationResult with validity status and suggestions
        """
        table = intent.get("table", "")
        operation = intent.get("operation", "")

        # Check if table exists in schema
        tables = self.domain.manifest.get("tables", {})
        if table and table not in tables:
            available_tables = self._get_table_descriptions()
            return ValidationResult(
                is_valid=False,
                reason=f"Table '{table}' not found in schema",
                suggestion=f"I can help with: {', '.join(available_tables.keys())}. Try asking about one of these."
            )

        # Check if operation is enabled
        if table and operation:
            table_meta = tables.get(table, {})
            operations = table_meta.get("operations", {})
            
            if operation == "create" and not operations.get("create", {}).get("enabled", False):
                return ValidationResult(
                    is_valid=False,
                    reason=f"Create operation not enabled for {table}",
                    suggestion=f"You can view {table} data, but creating new records is not available."
                )

        return ValidationResult(is_valid=True)

    def is_off_topic(self, message: str) -> bool:
        """
        Detect if query is outside domain scope.
        
        Args:
            message: User's message
            
        Returns:
            True if message appears off-topic
        """
        # Use simple keyword matching for now
        # Could be enhanced with LLM classification
        
        domain_keywords = self._extract_domain_keywords()
        message_lower = message.lower()
        
        # Check if message contains any domain-relevant keywords
        has_domain_keywords = any(keyword in message_lower for keyword in domain_keywords)
        
        # Check for common off-topic patterns
        off_topic_patterns = [
            r"\b(weather|news|sports|movie|recipe|joke)\b",
            r"\b(how are you|hello|hi|hey)\b",  # Greetings are OK, not off-topic
        ]
        
        has_off_topic = any(re.search(pattern, message_lower) for pattern in off_topic_patterns[:-1])
        
        # Off-topic if no domain keywords AND has off-topic patterns
        return has_off_topic and not has_domain_keywords

    def get_capabilities_summary(self) -> str:
        """
        Generate dynamic help based on domain schema.
        
        Returns:
            Human-readable capabilities summary
        """
        return self.get_help_response()

    def handle_inappropriate(self, message: str) -> str:
        """
        Clever response to inappropriate or off-topic input.
        
        Args:
            message: User's message
            
        Returns:
            Helpful redirect message
        """
        del message
        return (
            f"I can help with {self.domain_scope()}. "
            "Ask a direct domain question and I will answer briefly or say what evidence is missing."
        )

    def get_help_response(self) -> str:
        """
        Get comprehensive help response.
        
        Returns:
            Detailed help message
        """
        topics = self._domain_topics()
        examples = self._example_queries()

        lines = [
            f"Domain scope: {self.domain_scope()}.",
            f"Behavior: {self._behavior_summary()}",
        ]

        if topics:
            lines.append(f"Main entities: {', '.join(topics)}.")

        workflow_labels = self._workflow_labels()
        if workflow_labels:
            lines.append(f"Suggested actions: {', '.join(workflow_labels)}.")

        if examples:
            lines.append("Examples:")
            for example in examples[:4]:
                cleaned = str(example or "").strip()
                if cleaned:
                    lines.append(f"- {cleaned}")

        return "\n".join(lines).strip()

    def _extract_domain_keywords(self) -> List[str]:
        """Extract relevant keywords from domain schema."""
        keywords = []
        knowledge = self._domain_knowledge()
        for item in knowledge.get("primary_entities", []) if isinstance(knowledge, dict) else []:
            cleaned = str(item or "").strip()
            if cleaned:
                keywords.append(cleaned)

        # Add table names and aliases
        tables = self.domain.manifest.get("tables", {})
        for table_name, table_meta in tables.items():
            keywords.append(table_name.replace("_", " "))
            aliases = table_meta.get("aliases", [])
            keywords.extend(aliases)
        
        # Add important column names
        for table_meta in tables.values():
            important_cols = table_meta.get("important_columns", {})
            for col_name in important_cols.keys():
                keywords.append(col_name.replace("_", " "))
        
        return [k.lower() for k in keywords]

    def _get_table_descriptions(self) -> Dict[str, str]:
        """Get human-readable table descriptions."""
        capabilities = self.domain.get_capabilities()
        return capabilities.get("tables_description", {})
