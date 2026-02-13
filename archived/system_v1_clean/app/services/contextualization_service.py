import logging
import re
from typing import Any, List

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


class ContextualizationService:
    @staticmethod
    def _is_time_refinement_phrase(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False

        patterns = [
            r"^(?:for\s+)?(?:last|past)\s+\d+\s+(?:day|days|week|weeks|month|months|year|years)\b",
            r"^(?:for\s+)?(?:next)\s+\d+\s+(?:day|days|week|weeks|month|months|year|years)\b",
            r"^(?:for\s+)?(?:today|yesterday|tomorrow)\b",
            r"^(?:for\s+)?(?:this|last|next)\s+(?:week|month|year)\b",
            r"^(?:for\s+)?(?:last\s+30\s+days)\b",
        ]
        return any(re.search(pattern, lowered) for pattern in patterns)

    @staticmethod
    def is_refinement_only_query(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False

        # Time-only refinements like "for last 30 days".
        if ContextualizationService._is_time_refinement_phrase(lowered):
            return True

        # Short filter-only refinements like "status completed" or "only high priority".
        refinement_starts = ("for ", "with ", "only ", "status ", "priority ", "date ")
        if lowered.startswith(refinement_starts) and len(lowered.split()) <= 8:
            return True

        return False

    @staticmethod
    def is_self_contained_operational_query(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False

        # Explicit operational command + entity should not be rewritten with prior entity context.
        verb_pattern = r"\b(?:list|show|count|find|get|create|add|update|delete)\b"
        entity_pattern = r"\b(?:asset|assets|user|users|facility|facilities|task|tasks|company|companies)\b"
        if re.search(verb_pattern, lowered) and re.search(entity_pattern, lowered):
            return True

        # Single-entity asks like "assets"/"users" are already self-contained intents.
        if re.fullmatch(entity_pattern, lowered):
            return True

        return False

    @staticmethod
    def _extract_create_entity(previous_turn: str) -> str:
        m = re.search(r"(?:create|add)\s+(?:(?:an|a|new)\s+)?([a-z_]+)\b", previous_turn)
        if m:
            return m.group(1).strip()

        for candidate in ["asset", "user", "facility", "task", "company"]:
            if candidate in previous_turn:
                return candidate
        return ""

    @staticmethod
    def _looks_like_structured_slot_payload(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        # Typical follow-up details format: "asset name: X, category: Y"
        if ":" in lowered and any(k in lowered for k in ["name", "category", "type", "code", "id", "details"]):
            return True
        return False

    @staticmethod
    def format_history(messages: List[Any], max_turns: int = 4, max_chars: int = 200) -> str:
        history_msgs = messages[:-1][-max_turns:]
        lines: List[str] = []
        for msg in history_msgs:
            role = "User" if isinstance(msg, HumanMessage) else "Assistant"
            content = str(msg.content)
            if len(content) > max_chars:
                content = content[:max_chars] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def infer_deterministic_rewrite(messages: List[Any]) -> str:
        """
        Deterministic slot-fill fallback for short follow-up values.
        Example:
        Assistant: "What kind of asset are you looking to create?"
        User: "Coffee mug"
        -> "Create a new asset named Coffee mug"
        """
        if len(messages) < 2:
            return ""

        last_user = str(messages[-1].content).strip()
        previous_turn = str(messages[-2].content).strip().lower()
        if not last_user or "?" in last_user:
            return ""

        # Slot-fill path for create clarifications.
        if "?" in previous_turn:
            asks_create = any(k in previous_turn for k in ["create", "add", "new"])
            asks_slot = any(k in previous_turn for k in ["what kind", "what type", "name", "details"])
            if asks_create and asks_slot:
                entity = ContextualizationService._extract_create_entity(previous_turn)
                if not entity:
                    return ""

                # Structured slot payloads should be attached even if they contain several words.
                if ContextualizationService._looks_like_structured_slot_payload(last_user):
                    return f"Create a new {entity} with details: {last_user}"

                # Keep the original short-value slot-fill behavior.
                if len(last_user.split()) <= 5:
                    return f"Create a new {entity} named {last_user}"
                return ""

        # Refinement-only follow-up path.
        if not ContextualizationService.is_refinement_only_query(last_user):
            return ""

        previous_user_query = ""
        for msg in reversed(messages[:-1]):
            if isinstance(msg, HumanMessage):
                content = str(msg.content).strip()
                if content:
                    previous_user_query = content
                    break

        if not previous_user_query:
            return ""
        if ContextualizationService.is_refinement_only_query(previous_user_query):
            return ""

        return f"{previous_user_query} {last_user}".strip()

    def build_prompt(self, history_str: str, user_text: str) -> str:
        return f"""
You are a production query-rewriter for a multi-turn assistant.
Rewrite ONLY the latest user message so downstream routing/SQL can use it reliably.

Rules:
1. Preserve the user's intent exactly. Do not change meaning.
2. Resolve references (it, them, those, above result, that) using history.
3. If latest message is a short slot value (name/code/number) and assistant's prior turn is a clarification question, expand into a full actionable intent.
4. Keep entities, filters, and time constraints from prior turns only when user is clearly refining the same request and does not explicitly name a new entity.
5. If message is already self-contained, return it unchanged.
6. Do not ask questions. Do not add explanations. Output one single rewritten query line.
7. Preserve the user's language.
8. Never invent IDs, table names, or fields not present in conversation.
9. If the last user message is a short value (for example a name or code), attach it to the assistant's immediately previous question intent.
10. If no rewrite is needed, return the original user message exactly.

Chat History:
{history_str}

User's Last Question: {user_text}

Rewritten Question:
""".strip()
