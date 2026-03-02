"""Redis caching service for report results."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional

import redis.asyncio as redis

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

    def __init__(
        self,
        enabled: bool,
        default_ttl: int,
        redis_url: str,
        redis_client_factory: Callable[[str], redis.Redis],
    ):
        self.enabled = bool(enabled)
        self.default_ttl = max(1, int(default_ttl or 1))
        self.redis_url = str(redis_url or "").strip()
        self.redis_client_factory = redis_client_factory
        self.redis_client: Optional[redis.Redis] = None

        if self.enabled and self.redis_url:
            self._connect()
        elif self.enabled:
            logger.warning("Report cache disabled because REDIS_URL is empty")
            self.enabled = False

    def _connect(self) -> None:
        """Connect to Redis."""
        try:
            self.redis_client = self.redis_client_factory(self.redis_url)
            logger.info("Connected to Redis for report caching")
        except Exception:
            logger.exception("Failed to initialize Redis report cache client")
            self.redis_client = None
            self.enabled = False

    @staticmethod
    def _serialize_key_part(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [CacheService._serialize_key_part(item) for item in value]
        if isinstance(value, (set, frozenset)):
            normalized_items = [CacheService._serialize_key_part(item) for item in value]
            return sorted(
                normalized_items,
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            )
        if isinstance(value, dict):
            return {
                str(key): CacheService._serialize_key_part(item)
                for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
            }
        return {"__type__": type(value).__name__, "__value__": str(value)}

    def _resolve_ttl(self, ttl: Optional[int]) -> int:
        if ttl is None:
            return self.default_ttl
        return max(1, int(ttl))

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
        payload = {
            "report_id": str(report_id or "").strip(),
            "company_id": int(company_id),
            "page": int(page),
            "page_size": int(page_size),
            "params": self._serialize_key_part(params),
        }
        params_str = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        params_hash = hashlib.sha256(params_str.encode("utf-8")).hexdigest()[:16]
        return f"report:{payload['report_id']}:{payload['company_id']}:{payload['page']}:{payload['page_size']}:{params_hash}"

    async def _delete_matching(self, pattern: str) -> int:
        if not self.redis_client:
            return 0

        deleted = 0
        batch: list[str] = []
        async for key in self.redis_client.scan_iter(match=pattern):
            batch.append(key)
            if len(batch) >= 250:
                deleted += int(await self.redis_client.delete(*batch))
                batch.clear()

        if batch:
            deleted += int(await self.redis_client.delete(*batch))
        return deleted

    async def _count_matching(self, pattern: str) -> int:
        if not self.redis_client:
            return 0

        count = 0
        async for _ in self.redis_client.scan_iter(match=pattern):
            count += 1
        return count

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
            if cached is None:
                logger.debug("Report cache miss for key %s", cache_key)
                return None
            logger.debug("Report cache hit for key %s", cache_key)
            return json.loads(cached)
        except Exception:
            logger.exception("Report cache get error for key %s", cache_key)
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
            ttl_seconds = self._resolve_ttl(ttl)
            serialized = json.dumps(value, default=str)
            await self.redis_client.setex(
                cache_key,
                ttl_seconds,
                serialized,
            )
            logger.debug("Report cache set for key %s ttl=%ss", cache_key, ttl_seconds)
            return True
        except Exception:
            logger.exception("Report cache set error for key %s", cache_key)
            return False

    async def delete(self, cache_key: str) -> bool:
        """Delete cached value."""
        if not self.enabled or not self.redis_client:
            return False

        try:
            deleted = int(await self.redis_client.delete(cache_key))
            logger.debug("Report cache delete for key %s deleted=%s", cache_key, deleted)
            return deleted > 0
        except Exception:
            logger.exception("Report cache delete error for key %s", cache_key)
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
            normalized_report_id = str(report_id or "").strip()
            if company_id is not None:
                pattern = f"report:{normalized_report_id}:{int(company_id)}:*"
            else:
                pattern = f"report:{normalized_report_id}:*"

            deleted = await self._delete_matching(pattern)
            if deleted > 0:
                logger.info("Invalidated %s report cache entries for pattern %s", deleted, pattern)
            return deleted
        except Exception:
            logger.exception("Report cache invalidate error for report_id=%s", report_id)
            return 0

    async def clear_all(self) -> bool:
        """Clear all report caches (use with caution)."""
        if not self.enabled or not self.redis_client:
            return False

        try:
            deleted = await self._delete_matching("report:*")
            if deleted > 0:
                logger.warning("Cleared %s report cache entries", deleted)
            return True
        except Exception:
            logger.exception("Report cache clear error")
            return False

    async def get_stats(self) -> dict:
        """Get cache statistics."""
        if not self.enabled or not self.redis_client:
            return {"enabled": False}

        try:
            count = await self._count_matching("report:*")
            info = await self.redis_client.info("memory")
            return {
                "enabled": True,
                "total_keys": count,
                "memory_used_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
                "ttl_seconds": self.default_ttl,
            }
        except Exception as exc:
            logger.exception("Report cache stats error")
            return {"enabled": True, "error": str(exc)}
