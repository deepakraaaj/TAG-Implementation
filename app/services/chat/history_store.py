from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, List

from redis.exceptions import WatchError

from app.services.interfaces import CacheBackend

logger = logging.getLogger(__name__)

class ChatHistoryStore:
    """Session-scoped history persistence with consistent trimming/validation."""

    _ALLOWED_ROLES = frozenset({"user", "assistant"})
    _LOCK_STRIPES = 64
    _REDIS_TRANSACTION_RETRIES = 5

    def __init__(
        self,
        cache_backend: CacheBackend | None = None,
        ttl_seconds: int = 86400,
        max_messages: int = 100,
    ):
        if cache_backend is None:
            from app.services.platform.cache import cache as default_cache

            cache_backend = default_cache
        self.ttl_seconds = max(1, int(ttl_seconds or 1))
        self.max_messages = max(2, int(max_messages))
        self.cache: CacheBackend = cache_backend
        self._append_locks = [asyncio.Lock() for _ in range(self._LOCK_STRIPES)]

    def _history_key(self, session_id: str) -> str:
        return self.cache.generate_key("history", session_id)

    def _lock_for_session(self, session_id: str) -> asyncio.Lock:
        digest = hashlib.sha256(session_id.encode("utf-8")).digest()
        return self._append_locks[digest[0] % len(self._append_locks)]

    @staticmethod
    def _normalize_session_id(session_id: Any) -> str:
        return str(session_id or "").strip()

    def _normalize_entry(self, item: Any) -> Dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in self._ALLOWED_ROLES or not content:
            return None
        return {"role": role, "content": content}

    def _normalize_history(self, payload: Any) -> List[Dict[str, str]]:
        if not isinstance(payload, list):
            return []

        normalized = []
        for item in payload:
            entry = self._normalize_entry(item)
            if entry is not None:
                normalized.append(entry)

        if len(normalized) > self.max_messages:
            normalized = normalized[-self.max_messages :]
        return normalized

    async def load(self, session_id: str) -> List[Dict[str, str]]:
        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return []
        try:
            payload = await self.cache.get(self._history_key(normalized_session_id))
        except Exception:
            logger.exception("Failed to load chat history for session %s", normalized_session_id)
            return []
        return self._normalize_history(payload)

    async def save(self, session_id: str, history: List[Dict[str, str]]) -> None:
        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return
        trimmed = self._normalize_history(history)
        try:
            saved = await self.cache.set(
                self._history_key(normalized_session_id),
                trimmed,
                ttl=self.ttl_seconds,
            )
        except Exception:
            logger.exception("Failed to save chat history for session %s", normalized_session_id)
            return
        if not saved:
            logger.warning("Chat history cache write was not persisted for session %s", normalized_session_id)

    async def _append_turn_transactionally(
        self,
        session_id: str,
        user_entry: Dict[str, str] | None,
        assistant_entry: Dict[str, str] | None,
    ) -> List[Dict[str, str]] | None:
        redis_client = getattr(self.cache, "_redis", None)
        if redis_client is None:
            return None

        history_key = self._history_key(session_id)
        for _ in range(self._REDIS_TRANSACTION_RETRIES):
            try:
                async with redis_client.pipeline(transaction=True) as pipe:
                    await pipe.watch(history_key)
                    raw_payload = await pipe.get(history_key)
                    payload = json.loads(raw_payload) if raw_payload is not None else None
                    history = self._normalize_history(payload)
                    if user_entry is not None:
                        history.append(user_entry)
                    if assistant_entry is not None:
                        history.append(assistant_entry)
                    history = self._normalize_history(history)
                    pipe.multi()
                    pipe.setex(
                        history_key,
                        self.ttl_seconds,
                        json.dumps(history, default=str),
                    )
                    await pipe.execute()
                    return history
            except WatchError:
                continue
            except Exception:
                logger.exception("Failed Redis chat history append for session %s", session_id)
                return None

        logger.warning("Exceeded Redis chat history append retries for session %s", session_id)
        return None

    async def append_turn(self, session_id: str, user_message: Any, assistant_message: Any) -> List[Dict[str, str]]:
        normalized_session_id = self._normalize_session_id(session_id)
        if not normalized_session_id:
            return []

        lock = self._lock_for_session(normalized_session_id)
        async with lock:
            user_entry = self._normalize_entry({"role": "user", "content": user_message})
            assistant_entry = self._normalize_entry({"role": "assistant", "content": assistant_message})
            transactional_history = await self._append_turn_transactionally(
                normalized_session_id,
                user_entry,
                assistant_entry,
            )
            if transactional_history is not None:
                return transactional_history

            history = await self.load(normalized_session_id)
            if user_entry is not None:
                history.append(user_entry)
            if assistant_entry is not None:
                history.append(assistant_entry)
            history = self._normalize_history(history)
            await self.save(normalized_session_id, history)
            return history
