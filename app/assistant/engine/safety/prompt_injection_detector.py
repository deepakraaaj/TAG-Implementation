"""Security service for prompt injection detection and prevention."""
import logging
import re
from typing import Tuple

from app.domains.registry import DomainRegistry

logger = logging.getLogger(__name__)


class PromptInjectionDetector:
    """
    Detects and prevents prompt injection attacks.
    
    Protects against:
    - Role manipulation ("ignore previous instructions", "you are now...")
    - System prompt leakage attempts
    - Instruction override attempts
    - Jailbreak attempts
    """

    # Patterns that indicate prompt injection attempts
    INJECTION_PATTERNS = [
        # Role manipulation
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|commands?|prompts?)",
        r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|commands?)",
        r"forget\s+(all\s+)?(previous|prior|above)\s+(instructions?|commands?)",
        r"you\s+are\s+now\s+(a|an)\s+\w+",
        r"act\s+as\s+(a|an)\s+\w+",
        r"pretend\s+(you\s+are|to\s+be)\s+(a|an)?\s*\w+",
        r"roleplay\s+as",
        
        # System prompt extraction
        r"(show|display|print|reveal|tell\s+me)\s+(your\s+)?(system\s+)?(prompt|instructions?|rules)",
        r"what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?|rules)",
        r"repeat\s+(your\s+)?(system\s+)?(prompt|instructions?)",
        
        # Instruction override
        r"new\s+(instructions?|commands?|rules)",
        r"override\s+(instructions?|commands?|rules)",
        r"change\s+your\s+(instructions?|behavior|rules)",
        r"from\s+now\s+on",
        
        # Jailbreak attempts
        r"DAN\s+mode",
        r"developer\s+mode",
        r"sudo\s+mode",
        r"admin\s+mode",
        r"unrestricted\s+mode",
        
        # Delimiter injection
        r"<\|im_start\|>",
        r"<\|im_end\|>",
        r"###\s*System:",
        r"###\s*User:",
        r"###\s*Assistant:",
        
        # SQL injection in prompts (trying to manipulate SQL generation)
        r";\s*DROP\s+TABLE",
        r";\s*DELETE\s+FROM",
        r"UNION\s+SELECT",
        r"--\s*\w+",  # SQL comments
    ]

    @classmethod
    def detect(cls, user_input: str) -> Tuple[bool, str]:
        """
        Detect if user input contains prompt injection attempts.
        
        Args:
            user_input: User's message
            
        Returns:
            Tuple of (is_injection, reason)
        """
        if not user_input or not isinstance(user_input, str):
            return False, ""
        
        input_lower = user_input.lower()
        
        # Check each pattern
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, input_lower, re.IGNORECASE):
                logger.warning(f"Prompt injection detected: pattern='{pattern}' in input='{user_input[:100]}'")
                return True, f"Potential security violation detected"
        
        # Check for excessive special characters (potential delimiter injection)
        special_char_ratio = sum(1 for c in user_input if c in "<>|#$@{}[]") / max(len(user_input), 1)
        if special_char_ratio > 0.3:  # More than 30% special chars
            logger.warning(f"High special character ratio detected: {special_char_ratio:.2%}")
            return True, "Unusual input pattern detected"
        
        return False, ""

    @classmethod
    def sanitize(cls, user_input: str) -> str:
        """
        Sanitize user input by removing potentially dangerous patterns.
        
        Args:
            user_input: User's message
            
        Returns:
            Sanitized input
        """
        if not user_input:
            return user_input
        
        # Remove delimiter markers
        sanitized = re.sub(r"<\|im_start\|>|<\|im_end\|>", "", user_input)
        
        # Remove system/user/assistant markers
        sanitized = re.sub(r"###\s*(System|User|Assistant):", "", sanitized, flags=re.IGNORECASE)
        
        # Limit length to prevent token exhaustion attacks
        max_length = 2000
        if len(sanitized) > max_length:
            logger.warning(f"Input truncated from {len(sanitized)} to {max_length} chars")
            sanitized = sanitized[:max_length]
        
        return sanitized.strip()

    @classmethod
    def get_safe_error_message(cls) -> str:
        """
        Get a safe error message for injection attempts.
        
        Returns:
            User-friendly error message
        """
        default_message = (
            "I detected unusual patterns in your message that I can't process. "
            "Please rephrase your question in a straightforward way. "
            "I'm here to help with your configured domain workflows and data."
        )
        try:
            domain = DomainRegistry.get_current_domain()
            return domain.get_response_message("safe_injection_error", default=default_message)
        except Exception:
            return default_message
