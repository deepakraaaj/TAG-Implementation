import hashlib
import json
import logging
import time
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

class RedisCache:
    _instance: Optional["RedisCache"] = None
    _redis: Optional[redis.Redis] = None

    def __new__(cls, *args, **kwargs):
        use_singleton = kwargs.get("_singleton", True)
        if use_singleton:
            if cls._instance is None:
                cls._instance = super(RedisCache, cls).__new__(cls)
            return cls._instance
        return super(RedisCache, cls).__new__(cls)

    def __init__(
        self,
        redis_url: str | None = None,
        redis_client_factory=None,
        enable_memory_fallback: bool = True,
        _singleton: bool = True,
    ) -> None:
        if getattr(self, "_initialized", False) and _singleton:
            return
        self.redis_url = str(redis_url if redis_url is not None else get_settings().REDIS_URL or "").strip()
        self.redis_client_factory = redis_client_factory or redis.from_url
        self.enable_memory_fallback = bool(enable_memory_fallback)
        self._memory_fallback_active = False
        self._memory_store: dict[str, tuple[float | None, str]] = {}
        self._initialized = True

    async def connect(self):
        """Establish connection to Redis."""
        if self._redis is not None:
            return
        if not self.redis_url:
            self._activate_memory_fallback("REDIS_URL is empty")
            return

        client = self.redis_client_factory(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
            health_check_interval=30,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        try:
            await client.ping()
        except Exception:
            logger.exception("Failed to connect to Redis cache")
            await self._close_client(client)
            self._activate_memory_fallback(f"Redis unavailable at {self.redis_url}")
            return

        self._redis = client
        self._memory_fallback_active = False
        logger.info("Connected to Redis cache")

    def is_configured(self) -> bool:
        return bool(self.redis_url) or self.using_fallback()

    def is_connected(self) -> bool:
        return self._redis is not None or self.using_fallback()

    def using_fallback(self) -> bool:
        return bool(self._memory_fallback_active)

    async def ping(self) -> bool:
        client = self._redis
        if client is None:
            return self.using_fallback()
        try:
            return bool(await client.ping())
        except Exception:
            logger.debug("Redis cache ping failed", exc_info=True)
            await self._degrade_to_memory_fallback(client, reason="Redis ping failed")
            return self.using_fallback()

    async def close(self):
        """Close Redis connection."""
        client = self._redis
        self._redis = None
        self._memory_fallback_active = False
        self._memory_store.clear()
        if client is None:
            return

        await self._close_client(client)
        logger.info("Redis connection closed")

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from cache."""
        if not self._redis:
            return self._memory_get(key)
        try:
            val = await self._redis.get(key)
            if val is None:
                return None
            return json.loads(val)
        except Exception:
            logger.exception("Cache GET error for key %s", key)
            await self._degrade_to_memory_fallback(self._redis, reason="Redis GET failed")
            return self._memory_get(key)

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set a value in cache with TTL."""
        if not self._redis:
            return self._memory_set(key, value, ttl=ttl)
        try:
            ttl_seconds = max(1, int(ttl or 1))
            await self._redis.setex(
                key,
                ttl_seconds,
                json.dumps(value, default=str),
            )
            return True
        except Exception:
            logger.exception("Cache SET error for key %s", key)
            await self._degrade_to_memory_fallback(self._redis, reason="Redis SET failed")
            return self._memory_set(key, value, ttl=ttl)

    async def delete(self, key: str):
        """Delete a key from cache."""
        if not self._redis:
            return self._memory_delete(key)
        try:
            return await self._redis.delete(key)
        except Exception:
            logger.exception("Cache DELETE error for key %s", key)
            await self._degrade_to_memory_fallback(self._redis, reason="Redis DELETE failed")
            return self._memory_delete(key)

    @staticmethod
    def generate_key(prefix: str, *args) -> str:
        """Generate a consistent cache key."""
        serialized_args = json.dumps(
            [RedisCache._serialize_key_part(arg) for arg in args],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        hash_val = hashlib.sha256(serialized_args.encode("utf-8")).hexdigest()
        return f"{prefix}:{hash_val}"

    @staticmethod
    def _serialize_key_part(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [RedisCache._serialize_key_part(item) for item in value]
        if isinstance(value, (set, frozenset)):
            normalized_items = [RedisCache._serialize_key_part(item) for item in value]
            return sorted(
                normalized_items,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            )
        if isinstance(value, dict):
            return {
                str(key): RedisCache._serialize_key_part(item)
                for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
            }
        return {"__type__": type(value).__name__, "__value__": str(value)}

    @staticmethod
    async def _close_client(client: redis.Redis) -> None:
        try:
            await client.aclose()
        except AttributeError:
            await client.close()

    def _activate_memory_fallback(self, reason: str = "") -> None:
        if not self.enable_memory_fallback:
            return
        if not self._memory_fallback_active:
            detail = f" ({reason})" if str(reason or "").strip() else ""
            logger.warning("Using in-memory cache fallback%s", detail)
        self._memory_fallback_active = True

    async def _degrade_to_memory_fallback(self, client: Optional[redis.Redis], reason: str) -> None:
        if client is not None and client is self._redis:
            self._redis = None
        self._activate_memory_fallback(reason)
        if client is not None:
            await self._close_client(client)

    def _memory_get(self, key: str) -> Optional[Any]:
        self._activate_memory_fallback()
        entry = self._memory_store.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at is not None and expires_at <= time.monotonic():
            self._memory_store.pop(key, None)
            return None
        try:
            return json.loads(payload)
        except Exception:
            logger.exception("In-memory cache decode error for key %s", key)
            self._memory_store.pop(key, None)
            return None

    def _memory_set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        self._activate_memory_fallback()
        try:
            ttl_seconds = max(1, int(ttl or 1))
            expires_at = time.monotonic() + ttl_seconds
            self._memory_store[key] = (expires_at, json.dumps(value, default=str))
            return True
        except Exception:
            logger.exception("In-memory cache SET error for key %s", key)
            return False

    def _memory_delete(self, key: str) -> int:
        self._activate_memory_fallback()
        existed = key in self._memory_store
        self._memory_store.pop(key, None)
        return 1 if existed else 0

# Global instance
cache = RedisCache()
