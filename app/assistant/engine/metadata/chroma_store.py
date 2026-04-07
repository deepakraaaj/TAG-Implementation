from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from app.assistant.engine.metadata.semantic_chunker import SemanticBundleChunker

logger = logging.getLogger(__name__)


class ChromaSemanticStore:
    def __init__(
        self,
        *,
        domain_provider: Callable[[], Any],
        model_name: str,
        persist_path: str,
    ) -> None:
        self.domain_provider = domain_provider
        self.model_name = str(model_name or "").strip()
        self.persist_path = Path(str(persist_path or "").strip() or "./output/chromadb")
        self._client: Any = None
        self._embedding_function: Any = None
        self._chunker = SemanticBundleChunker(domain_provider)

    def is_available(self) -> bool:
        try:
            self._ensure_client()
            self._ensure_embedding_function()
            return True
        except Exception as exc:
            logger.warning("Chroma semantic store unavailable: %s", exc)
            return False

    def bundle_available(self) -> bool:
        return self._chunker.has_bundle()

    def reindex_domain(self) -> int:
        chunks = self._chunker.build_chunks()
        if not chunks:
            return 0

        collection = self._base_collection()
        ids = [item["id"] for item in chunks]
        documents = [item["text"] for item in chunks]
        metadatas = [self._metadata(item) for item in chunks]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)

    def search(
        self,
        query: str,
        *,
        kinds: Optional[Set[str]] = None,
        limit: int = 6,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        query_text = str(query or "").strip()
        if not query_text:
            return []
        if not self.bundle_available():
            return []

        base_collection = self._base_collection()
        learned_collection = self._learned_collection()
        max_hits = max(1, int(limit or 6))
        query_limit = max(max_hits * 3, 10)
        normalized_kinds = {str(kind).strip().lower() for kind in (kinds or set()) if str(kind).strip()}

        results: List[Dict[str, Any]] = []
        for collection in (base_collection, learned_collection):
            try:
                response = collection.query(query_texts=[query_text], n_results=query_limit)
            except Exception:
                continue
            ids = (response.get("ids") or [[]])[0]
            documents = (response.get("documents") or [[]])[0]
            metadatas = (response.get("metadatas") or [[]])[0]
            distances = (response.get("distances") or [[]])[0]
            for item_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
                payload = dict(metadata or {})
                kind = str(payload.get("kind") or "").strip().lower()
                if normalized_kinds and kind not in normalized_kinds:
                    continue
                score = max(0.0, 1.0 - float(distance or 0.0))
                if score < float(min_score or 0.0):
                    continue
                results.append(
                    {
                        "id": str(item_id or "").strip(),
                        "kind": kind,
                        "artifact_id": str(payload.get("artifact_id") or "").strip(),
                        "text": str(document or "").strip(),
                        "candidate_tables": self._metadata_tables(payload),
                        "route": str(payload.get("route") or "").strip(),
                        "source_file": str(payload.get("source_file") or "").strip(),
                        "sql": str(payload.get("sql") or "").strip(),
                        "score": score,
                    }
                )

        deduped: Dict[str, Dict[str, Any]] = {}
        for item in sorted(results, key=lambda value: float(value.get("score") or 0.0), reverse=True):
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in deduped:
                continue
            deduped[item_id] = item
            if len(deduped) >= max_hits:
                break
        return list(deduped.values())

    def remember_success(
        self,
        *,
        question: str,
        sql: str,
        candidate_tables: Optional[Sequence[str]] = None,
    ) -> str:
        question_text = str(question or "").strip()
        sql_text = str(sql or "").strip()
        if not question_text or not sql_text:
            return ""

        identifier = hashlib.sha1(f"{question_text}::{sql_text}".encode("utf-8")).hexdigest()
        document = f"successful nl2sql example question {question_text} | sql {sql_text}"
        metadata = {
            "kind": "learned_query",
            "artifact_id": identifier,
            "route": "SQL",
            "source_file": "runtime_memory",
            "candidate_tables": json.dumps([str(item).strip() for item in (candidate_tables or []) if str(item).strip()]),
            "sql": sql_text,
        }
        self._learned_collection().upsert(
            ids=[identifier],
            documents=[document],
            metadatas=[metadata],
        )
        return identifier

    def _ensure_client(self):
        if self._client is None:
            import chromadb

            self.persist_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.persist_path))
        return self._client

    def _ensure_embedding_function(self):
        if self._embedding_function is None:
            from chromadb.utils.embedding_functions import FastembedEmbeddingFunction

            self._embedding_function = FastembedEmbeddingFunction(model_name=self.model_name)
        return self._embedding_function

    def _domain_name(self) -> str:
        domain = self.domain_provider()
        name = str(getattr(domain, "name", "") or getattr(domain, "_domain_name", "") or "default").strip()
        return name or "default"

    def _collection_name(self, suffix: str) -> str:
        domain_name = self._domain_name().replace("-", "_")
        return f"tag_{suffix}_{domain_name}"

    def _base_collection(self):
        return self._ensure_client().get_or_create_collection(
            name=self._collection_name("semantic"),
            embedding_function=self._ensure_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )

    def _learned_collection(self):
        return self._ensure_client().get_or_create_collection(
            name=self._collection_name("learned_sql"),
            embedding_function=self._ensure_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": str(item.get("kind") or "").strip(),
            "artifact_id": str(item.get("artifact_id") or "").strip(),
            "route": str(item.get("route") or "").strip(),
            "source_file": str(item.get("source_file") or "").strip(),
            "candidate_tables": json.dumps(item.get("candidate_tables") or []),
            "sql": str(item.get("sql") or "").strip(),
        }

    @staticmethod
    def _metadata_tables(metadata: Dict[str, Any]) -> List[str]:
        raw = metadata.get("candidate_tables")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = []
            if isinstance(payload, list):
                return [str(item).strip() for item in payload if str(item).strip()]
        return []

