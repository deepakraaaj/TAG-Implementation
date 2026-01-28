import os
import logging
from typing import List, Dict, Any, Optional
from elasticsearch import AsyncElasticsearch, TransportError
from fastembed import TextEmbedding
import asyncio
from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ES_URL = settings.ELASTICSEARCH_URL

class VectorService:
    _instance: Optional['VectorService'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(VectorService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'es'):
            self.es = AsyncElasticsearch(
                hosts=[ES_URL],
                request_timeout=10,
                retry_on_timeout=True
            )
            # Use fastembed (lightweight ONNX runtime)
            # BAAl/bge-small-en-v1.5 is excellent and small, or use all-MiniLM-L6-v2
            logger.info("Loading fastembed model: BAAI/bge-small-en-v1.5")
            self.embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
            self.embedding_dim = 384  # bge-small-en-v1.5 dimension
            self.index_name = "tag_knowledge_base"
            logger.info(f"✅ FastEmbed ready (dim={self.embedding_dim})")

    async def check_health(self) -> bool:
        """Verify ES connection."""
        try:
            return await self.es.ping()
        except Exception as e:
            logger.error(f"ES Health Check Failed: {e}")
            return False

    async def search_semantic(self, query: str, limit: int = 3, filters: Dict[str, Any] = None) -> List[Dict]:
        """
        Perform semantic search using vector embeddings.
        """
        try:
            # 1. Generate Embedding
            # FastEmbed is fast, but let's run in executor to be safe for async
            loop = asyncio.get_event_loop()
            
            # fastembed returns a generator, we take the first item
            def get_vector():
                return list(self.embedding_model.embed([query]))[0].tolist()

            vector = await loop.run_in_executor(None, get_vector)
            
            # 2. Search Elasticsearch (Using script_score for ES 7.x compatibility)
            # Implement PRE-FILTERING (Filter first, then vector score)
            
            base_query = {"match_all": {}}
            
            # 2. Search Elasticsearch
            # Construct top-level Boolean Query
            # Must match the filter AND the script score
            
            script_score_query = {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {"query_vector": vector}
                    }
                }
            }

            final_query = script_score_query
            
            if filters:
                filter_clauses = []
                for key, value in filters.items():
                    filter_clauses.append({"term": {f"metadata.{key}.keyword": value}})
                
                if filter_clauses:
                    final_query = {
                        "bool": {
                            "must": [script_score_query],
                            "filter": filter_clauses
                        }
                    }

            query_body = {
                "size": limit,
                "query": final_query,
                "_source": ["content", "metadata", "title"]
            }
            
            response = await self.es.search(
                index=self.index_name,
                body=query_body
            )
            
            results = []
            for hit in response["hits"]["hits"]:
                results.append({
                    "content": hit["_source"].get("content", ""),
                    "metadata": hit["_source"].get("metadata", {}),
                    "score": hit["_score"]
                })
                
            return results
            
        except TransportError as e:
            logger.error(f"Elasticsearch Transport Error: {e}")
            return []
        except Exception as e:
            logger.error(f"Vector Search Failed: {e}")
            return []

    async def index_document(self, content: str, metadata: Dict = None):
        """
        Index a document for semantic search.
        """
        try:
            # Generate embedding
            loop = asyncio.get_event_loop()
            
            def get_vector():
                return list(self.embedding_model.embed([content]))[0].tolist()

            vector = await loop.run_in_executor(None, get_vector)
            
            doc = {
                "content": content,
                "metadata": metadata or {},
                "embedding": vector
            }
            
            await self.es.index(index=self.index_name, document=doc)
            logger.info("Document indexed successfully")
            
        except Exception as e:
            logger.error(f"Indexing failed: {e}")

# Global instance
vector_service = VectorService()
