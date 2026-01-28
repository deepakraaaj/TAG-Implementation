import redis.asyncio as redis # type: ignore
import json
import logging
from typing import Optional
from ..config import get_settings

logger = logging.getLogger(__name__)

class SemanticCache:
    def __init__(self):
        self.redis_url = get_settings().REDIS_URL
        self.redis = redis.from_url(self.redis_url, decode_responses=True)
        # Note: True vector similarity search requires RediSearch module and vector indexing.
        # For this basic implementation, we will simulate or use exact match 
        # as a placeholder until RediSearch setup is confirmed/configured.
        # To strictly follow the plan (Vector Similarity), we would need 
        # to generate embeddings here and query Redis.
        
    async def get(self, query: str) -> Optional[str]:
        """
        Retrieves a cached response for a given query.
        Currently implements exact match for simplicity. 
        TODO: Upgrade to vector similarity search.
        """
        # Normalize query slightly
        key = f"cache:{query.strip().lower()}"
        try:
            return await self.redis.get(key)
        except Exception as e:
            logger.warning(f"Semantic Cache GET failed: {e}")
            return None

    async def set(self, query: str, response: str, ttl: int = 3600):
        """
        Caches a response for a given query.
        """
        key = f"cache:{query.strip().lower()}"
        try:
            await self.redis.set(key, response, ex=ttl)
        except Exception as e:
            logger.warning(f"Semantic Cache SET failed: {e}")

    async def close(self):
        await self.redis.close()
