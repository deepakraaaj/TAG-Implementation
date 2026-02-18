"""Redis caching service for report results."""
import json
import hashlib
import logging
from typing import Any, Optional
import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class CacheService:
    """
    Redis-based caching service for report results.
    
    Features:
    - Automatic cache key generation
    - Configurable TTL per report
    - JSON serialization
    - Cache invalidation
    """

    def __init__(self):
        self.enabled = settings.CACHE_ENABLED
        self.default_ttl = settings.CACHE_TTL_SECONDS
        self.redis_client: Optional[redis.Redis] = None
        
        if self.enabled:
            self._connect()

    def _connect(self):
        """Connect to Redis."""
        try:
            self.redis_client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
            logger.info("Connected to Redis for caching")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.enabled = False

    def generate_cache_key(
        self,
        report_id: str,
        company_id: int,
        page: int,
        page_size: int,
        **params
    ) -> str:
        """
        Generate unique cache key for report.
        
        Format: report:{report_id}:{company_id}:{page}:{page_size}:{params_hash}
        """
        # Create hash of additional parameters
        params_str = json.dumps(params, sort_keys=True)
        params_hash = hashlib.md5(params_str.encode()).hexdigest()[:8]
        
        return f"report:{report_id}:{company_id}:{page}:{page_size}:{params_hash}"

    async def get(self, cache_key: str) -> Optional[Any]:
        """
        Get cached value.
        
        Returns:
            Cached data if exists, None otherwise
        """
        if not self.enabled or not self.redis_client:
            return None

        try:
            cached = await self.redis_client.get(cache_key)
            if cached:
                logger.info(f"Cache HIT: {cache_key}")
                return json.loads(cached)
            else:
                logger.info(f"Cache MISS: {cache_key}")
                return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    async def set(
        self,
        cache_key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set cached value with TTL.
        
        Args:
            cache_key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (default: CACHE_TTL_SECONDS)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.redis_client:
            return False

        try:
            ttl = ttl or self.default_ttl
            serialized = json.dumps(value)
            
            await self.redis_client.setex(
                cache_key,
                ttl,
                serialized
            )
            logger.info(f"Cache SET: {cache_key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    async def delete(self, cache_key: str) -> bool:
        """Delete cached value."""
        if not self.enabled or not self.redis_client:
            return False

        try:
            await self.redis_client.delete(cache_key)
            logger.info(f"Cache DELETE: {cache_key}")
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False

    async def invalidate_report(
        self,
        report_id: str,
        company_id: Optional[int] = None
    ) -> int:
        """
        Invalidate all cache entries for a report.
        
        Args:
            report_id: Report identifier
            company_id: Optional company filter
            
        Returns:
            Number of keys deleted
        """
        if not self.enabled or not self.redis_client:
            return 0

        try:
            # Build pattern
            if company_id:
                pattern = f"report:{report_id}:{company_id}:*"
            else:
                pattern = f"report:{report_id}:*"
            
            # Find matching keys
            keys = []
            async for key in self.redis_client.scan_iter(match=pattern):
                keys.append(key)
            
            # Delete keys
            if keys:
                deleted = await self.redis_client.delete(*keys)
                logger.info(f"Cache INVALIDATE: {pattern} ({deleted} keys)")
                return deleted
            
            return 0
        except Exception as e:
            logger.error(f"Cache invalidate error: {e}")
            return 0

    async def clear_all(self) -> bool:
        """Clear all report caches (use with caution)."""
        if not self.enabled or not self.redis_client:
            return False

        try:
            # Find all report cache keys
            keys = []
            async for key in self.redis_client.scan_iter(match="report:*"):
                keys.append(key)
            
            if keys:
                await self.redis_client.delete(*keys)
                logger.warning(f"Cache CLEAR ALL: {len(keys)} keys deleted")
            
            return True
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return False

    async def get_stats(self) -> dict:
        """Get cache statistics."""
        if not self.enabled or not self.redis_client:
            return {"enabled": False}

        try:
            # Count report cache keys
            count = 0
            async for _ in self.redis_client.scan_iter(match="report:*"):
                count += 1
            
            # Get Redis info
            info = await self.redis_client.info("memory")
            
            return {
                "enabled": True,
                "total_keys": count,
                "memory_used_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
                "ttl_seconds": self.default_ttl
            }
        except Exception as e:
            logger.error(f"Cache stats error: {e}")
            return {"enabled": True, "error": str(e)}
