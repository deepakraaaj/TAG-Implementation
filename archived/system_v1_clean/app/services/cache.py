import json
import logging
from typing import Optional, Any
import redis.asyncio as redis
import hashlib

from ..config import get_settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = get_settings().REDIS_URL

class RedisCache:
    _instance: Optional['RedisCache'] = None
    _redis: Optional[redis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisCache, cls).__new__(cls)
        return cls._instance

    async def connect(self):
        """Establish connection to Redis."""
        if not self._redis:
            try:
                self._redis = redis.from_url(
                    REDIS_URL, 
                    encoding="utf-8", 
                    decode_responses=True,
                    max_connections=10
                )
                await self._redis.ping()
                logger.info("✅ Connected to Redis Cache")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Redis: {e}")
                self._redis = None

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            logger.info("Redis connection closed")

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from cache."""
        if not self._redis:
            return None
        try:
            val = await self._redis.get(key)
            if val:
                return json.loads(val)
            return None
        except Exception as e:
            logger.error(f"Cache GET error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set a value in cache with TTL."""
        if not self._redis:
            return False
        try:
            await self._redis.setex(
                key,
                ttl,
                json.dumps(value, default=str)
            )
            return True
        except Exception as e:
            logger.error(f"Cache SET error: {e}")
            return False
            
    async def delete(self, key: str):
        """Delete a key from cache."""
        if self._redis:
            await self._redis.delete(key)

    @staticmethod
    def generate_key(prefix: str, *args) -> str:
        """Generate a consistent cache key."""
        payload = "".join(str(arg) for arg in args)
        hash_val = hashlib.sha256(payload.encode()).hexdigest()
        return f"{prefix}:{hash_val}"

# Global instance
cache = RedisCache()
