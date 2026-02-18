"""Intelligent response system for request validation and capability discovery."""
import logging
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from app.config import get_settings
from app.domains.registry import DomainRegistry

logger = logging.getLogger(__name__)
settings = get_settings()


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

    def __init__(self, domain_registry: Optional[DomainRegistry] = None):
        """Initialize with domain registry."""
        self.domain = domain_registry or DomainRegistry.get_current_domain()
        self.llm = ChatOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            temperature=0.3,
            timeout=settings.LLM_TIMEOUT,
            max_retries=settings.LLM_MAX_RETRIES,
        )

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
        capabilities = self.domain.get_capabilities()
        description = capabilities.get("description", "I'm here to help!")
        examples = capabilities.get("examples", [])
        
        summary = f"{description}\n\n"
        
        if examples:
            summary += "**Here are some things you can try:**\n"
            for example in examples[:5]:  # Limit to 5 examples
                summary += f"- {example}\n"
        
        return summary.strip()

    def handle_inappropriate(self, message: str) -> str:
        """
        Clever response to inappropriate or off-topic input.
        
        Args:
            message: User's message
            
        Returns:
            Helpful redirect message
        """
        capabilities = self.domain.get_capabilities()
        description = capabilities.get("description", "I'm a specialized assistant")
        
        return (
            f"I appreciate your message, but {description.lower()}. "
            f"Let me know if you'd like to know what I can help with!"
        )

    def get_help_response(self) -> str:
        """
        Get comprehensive help response.
        
        Returns:
            Detailed help message
        """
        capabilities = self.domain.get_capabilities()
        bot_name = self.domain.config.get("bot_name", "Assistant")
        description = self.domain.description
        examples = capabilities.get("examples", [])
        categorized_examples = capabilities.get("categorized_examples", {})
        tables_desc = capabilities.get("tables_description", {})
        
        response = f"**{description}**\n\n"
        
        if tables_desc:
            response += "**Available Data:**\n"
            for table, desc in list(tables_desc.items())[:5]:  # Limit to 5
                response += f"- **{table.replace('_', ' ').title()}**: {desc}\n"
            response += "\n"
        
        if categorized_examples:
            response += "**What I can do:**\n\n"
            for category, qs in categorized_examples.items():
                response += f"**{category}**\n"
                for q in qs:
                    response += f"- {q}\n"
                response += "\n"
        elif examples:
            response += "**Example Queries:**\n"
            for example in examples[:8]:  # Show more examples
                response += f"- {example}\n"
        
        return response.strip()

    def _extract_domain_keywords(self) -> List[str]:
        """Extract relevant keywords from domain schema."""
        keywords = []
        
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
