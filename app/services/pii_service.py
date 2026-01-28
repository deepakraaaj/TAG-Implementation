import re
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

class PIIService:
    def __init__(self):
        # Basic regex patterns for frequent PII
        self.patterns = {
            "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "PHONE": r"\b(?:\+?\d{1,3}[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b",
            # Add more patterns as needed
        }

    def sanitize(self, text: str) -> str:
        """
        Redacts PII from the input text using defined regex patterns.
        Replaces found PII with <[TYPE]>.
        """
        sanitized_text = text
        for pii_type, pattern in self.patterns.items():
            sanitized_text = re.sub(pattern, f"<{pii_type}>", sanitized_text)
        
        if sanitized_text != text:
            logger.info("PII redacted from input text.")
            
        return sanitized_text

    def analyze(self, text: str) -> List[Dict[str, str]]:
        """
        Returns a list of detected PII entities.
        """
        results = []
        for pii_type, pattern in self.patterns.items():
            matches = re.finditer(pattern, text)
            for match in matches:
                results.append({
                    "type": pii_type,
                    "value": match.group(),
                    "start": match.start(),
                    "end": match.end()
                })
        return results
