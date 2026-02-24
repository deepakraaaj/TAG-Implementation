from __future__ import annotations

from typing import Any, Dict, List

from app.services.cache import cache


class ChatHistoryStore:
    """Session-scoped history persistence with consistent trimming/validation."""

    def __init__(self, ttl_seconds: int = 86400, max_messages: int = 100):
        self.ttl_seconds = int(ttl_seconds)
        self.max_messages = max(2, int(max_messages))

    @staticmethod
    def _history_key(session_id: str) -> str:
        return cache.generate_key("history", session_id)

    async def load(self, session_id: str) -> List[Dict[str, str]]:
        payload = await cache.get(self._history_key(session_id))
        if not isinstance(payload, list):
            return []
        normalized: List[Dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            normalized.append({"role": role, "content": content})
        if len(normalized) > self.max_messages:
            normalized = normalized[-self.max_messages :]
        return normalized

    async def save(self, session_id: str, history: List[Dict[str, str]]) -> None:
        trimmed = history[-self.max_messages :]
        await cache.set(self._history_key(session_id), trimmed, ttl=self.ttl_seconds)

    async def append_turn(self, session_id: str, user_message: Any, assistant_message: Any) -> List[Dict[str, str]]:
        history = await self.load(session_id)
        user_text = str(user_message or "").strip()
        assistant_text = str(assistant_message or "").strip()
        if user_text:
            history.append({"role": "user", "content": user_text})
        if assistant_text:
            history.append({"role": "assistant", "content": assistant_text})
        await self.save(session_id, history)
        return history
