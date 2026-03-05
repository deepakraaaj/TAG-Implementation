import hashlib
import json
import logging
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

class RedisCache:
    _instance: Optional["RedisCache"] = None
    _redis: Optional[redis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self.redis_url = str(get_settings().REDIS_URL or "").strip()
        self._initialized = True

    async def connect(self):
        """Establish connection to Redis."""
        if self._redis is not None:
            return
        if not self.redis_url:
            logger.warning("Redis cache disabled because REDIS_URL is empty")
            return

        client = redis.from_url(
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
            return

        self._redis = client
        logger.info("Connected to Redis cache")

    def is_configured(self) -> bool:
        return bool(self.redis_url)

    def is_connected(self) -> bool:
        return self._redis is not None

    async def ping(self) -> bool:
        client = self._redis
        if client is None:
            return False
        try:
            return bool(await client.ping())
        except Exception:
            logger.debug("Redis cache ping failed", exc_info=True)
            return False

    async def close(self):
        """Close Redis connection."""
        client = self._redis
        self._redis = None
        if client is None:
            return

        await self._close_client(client)
        logger.info("Redis connection closed")

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from cache."""
        if not self._redis:
            return None
        try:
            val = await self._redis.get(key)
            if val is None:
                return None
            return json.loads(val)
        except Exception:
            logger.exception("Cache GET error for key %s", key)
            return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set a value in cache with TTL."""
        if not self._redis:
            return False
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
            return False

    async def delete(self, key: str):
        """Delete a key from cache."""
        if not self._redis:
            return
        try:
            await self._redis.delete(key)
        except Exception:
            logger.exception("Cache DELETE error for key %s", key)

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

# Global instance
cache = RedisCache()
