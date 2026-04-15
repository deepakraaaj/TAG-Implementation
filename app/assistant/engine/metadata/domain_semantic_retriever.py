from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.assistant.engine.metadata.chroma_store import ChromaSemanticStore
from app.assistant.engine.metadata.semantic_chunker import SemanticBundleChunker
from app.config import get_settings

logger = logging.getLogger(__name__)


class _FastEmbedAdapter:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._embedder = TextEmbedding(model_name=str(model_name or "").strip())

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self._embedder.embed([str(text or "") for text in texts])
        return [list(vector.tolist()) for vector in vectors]


class DomainSemanticRetriever:
    _artifact_cache: Dict[str, List[Dict[str, Any]]] = {}
    _vector_cache: Dict[str, List[List[float]]] = {}
    _indexed_domains: Set[str] = set()

    def __init__(
        self,
        domain_provider: Callable[[], Any],
        *,
        enabled: Optional[bool] = None,
        embedder_factory: Optional[Callable[[], Any]] = None,
        chroma_store: Optional[Any] = None,
    ):
        settings = get_settings()
        self.domain_provider = domain_provider
        self.enabled = settings.SEMANTIC_RETRIEVAL_ENABLED if enabled is None else bool(enabled)
        self.model_name = str(settings.SEMANTIC_RETRIEVAL_MODEL or "").strip()
        self.default_top_k = int(settings.SEMANTIC_RETRIEVAL_TOP_K or 6)
        self.default_prompt_k = int(settings.SEMANTIC_RETRIEVAL_PROMPT_K or 6)
        self.default_min_score = float(settings.SEMANTIC_RETRIEVAL_MIN_SCORE or 0.0)
        self.route_min_score = float(settings.SEMANTIC_RETRIEVAL_ROUTE_MIN_SCORE or 0.0)
        self.provider = str(settings.SEMANTIC_RETRIEVAL_PROVIDER or "fastembed").strip().lower()
        self._embedder_factory = embedder_factory or self._default_embedder_factory
        self._embedder: Any = None
        self._chunker = SemanticBundleChunker(domain_provider)
        self._chroma_store = chroma_store
        if self._chroma_store is None and self.provider == "chroma":
            self._chroma_store = ChromaSemanticStore(
                domain_provider=domain_provider,
                model_name=self.model_name,
                persist_path=str(settings.SEMANTIC_RETRIEVAL_CHROMA_PATH or "").strip(),
            )

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    def _default_embedder_factory(self):
        return _FastEmbedAdapter(self.model_name)

    def _chroma_available(self) -> bool:
        store = getattr(self, "_chroma_store", None)
        return bool(store is not None and hasattr(store, "is_available") and store.is_available())

    def _ensure_embedder(self):
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        return self._embedder

    def _domain(self):
        return self.domain_provider()

    def _domain_key(self) -> str:
        try:
            domain = self._domain()
        except Exception:
            domain = None
        domain_name = str(getattr(domain, "name", "") or getattr(domain, "_domain_name", "") or "default").strip()
        return f"{domain_name or 'default'}::{self.provider}::{self.model_name}"

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def _dedupe_tables(cls, values: Sequence[str]) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for value in values:
            text_value = cls._normalize_text(value)
            lowered = text_value.lower()
            if not text_value or lowered in seen:
                continue
            seen.add(lowered)
            out.append(text_value)
        return out

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for left, right in zip(a, b):
            dot += float(left) * float(right)
            norm_a += float(left) * float(left)
            norm_b += float(right) * float(right)
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))

    def _available_tables(self) -> Set[str]:
        try:
            domain = self._domain()
        except Exception:
            return set()
        manifest = dict(getattr(domain, "manifest", {}) or {})
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        return {str(name).strip() for name in tables.keys() if str(name).strip()}

    def _candidate_tables_from_text(self, text: str, available_tables: Set[str]) -> List[str]:
        lowered = self._normalize_text(text).lower()
        matches = [table for table in sorted(available_tables) if re.search(rf"\b{re.escape(table.lower())}\b", lowered)]
        return self._dedupe_tables(matches)

    def _table_artifacts(self, domain: Any, available_tables: Set[str]) -> List[Dict[str, Any]]:
        manifest = dict(getattr(domain, "manifest", {}) or {})
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        artifacts: List[Dict[str, Any]] = []
        for table_name, meta in sorted(tables.items()):
            if not isinstance(meta, dict):
                continue
            aliases = [self._normalize_text(alias) for alias in (meta.get("aliases") or []) if self._normalize_text(alias)]
            description = self._normalize_text(meta.get("description"))
            columns = [
                self._normalize_text(column)
                for column in (meta.get("important_columns") or {}).keys()
                if self._normalize_text(column)
            ]
            joins = meta.get("joins") if isinstance(meta.get("joins"), dict) else {}
            join_text = "; ".join(f"{left} -> {right}" for left, right in joins.items())
            text_parts = [
                f"table {table_name}",
                f"aliases {'; '.join(aliases)}" if aliases else "",
                f"description {description}" if description else "",
                f"columns {'; '.join(columns)}" if columns else "",
                f"joins {join_text}" if join_text else "",
            ]
            artifacts.append(
                {
                    "kind": "table",
                    "artifact_id": table_name,
                    "text": " | ".join(part for part in text_parts if part),
                    "candidate_tables": [table_name],
                    "route": "SQL",
                }
            )
        return artifacts

    def _example_artifacts(self, domain: Any, available_tables: Set[str]) -> List[Dict[str, Any]]:
        config = getattr(getattr(domain, "spec", None), "config", None)
        few_shots = list(getattr(config, "few_shot_examples", []) or [])
        artifacts: List[Dict[str, Any]] = []
        for index, shot in enumerate(few_shots):
            if not isinstance(shot, dict):
                continue
            question = self._normalize_text(shot.get("question"))
            intent = shot.get("intent") if isinstance(shot.get("intent"), dict) else {}
            table = self._normalize_text(intent.get("table"))
            joins = [self._normalize_text(item) for item in (intent.get("joins") or []) if self._normalize_text(item)]
            columns = [self._normalize_text(item) for item in (intent.get("columns") or []) if self._normalize_text(item)]
            filters = intent.get("filters") if isinstance(intent.get("filters"), list) else []
            filter_text = []
            for item in filters:
                if not isinstance(item, dict):
                    continue
                field = self._normalize_text(item.get("field"))
                value = self._normalize_text(item.get("value"))
                if field or value:
                    filter_text.append(f"{field}={value}".strip("="))
            candidate_tables = self._dedupe_tables([table, *joins])
            if not candidate_tables:
                candidate_tables = self._candidate_tables_from_text(json.dumps(intent, sort_keys=True), available_tables)
            text_parts = [
                f"example question {question}" if question else "",
                f"table {table}" if table else "",
                f"joins {'; '.join(joins)}" if joins else "",
                f"columns {'; '.join(columns)}" if columns else "",
                f"filters {'; '.join(filter_text)}" if filter_text else "",
            ]
            artifacts.append(
                {
                    "kind": "example",
                    "artifact_id": f"example_{index}",
                    "text": " | ".join(part for part in text_parts if part),
                    "candidate_tables": candidate_tables,
                    "route": "SQL",
                }
            )
        return artifacts

    def _glossary_artifacts(self, domain: Any, available_tables: Set[str]) -> List[Dict[str, Any]]:
        glossary = dict(getattr(getattr(domain, "spec", None), "language", None).glossary or {}) if getattr(getattr(domain, "spec", None), "language", None) is not None else {}
        artifacts: List[Dict[str, Any]] = []
        for term, meaning in sorted(glossary.items()):
            term_text = self._normalize_text(term)
            meaning_text = self._normalize_text(meaning)
            if not term_text or not meaning_text:
                continue
            candidate_tables = self._candidate_tables_from_text(meaning_text, available_tables)
            artifacts.append(
                {
                    "kind": "term",
                    "artifact_id": term_text,
                    "text": f"business term {term_text} means {meaning_text}",
                    "candidate_tables": candidate_tables,
                    "route": "SQL",
                }
            )
        return artifacts

    def _special_query_artifacts(self, domain: Any, available_tables: Set[str]) -> List[Dict[str, Any]]:
        sql_builder = domain.get_config_section("sql_builder") if hasattr(domain, "get_config_section") else {}
        queries = sql_builder.get("special_queries") if isinstance(sql_builder, dict) else []
        artifacts: List[Dict[str, Any]] = []
        if not isinstance(queries, list):
            return artifacts
        for index, item in enumerate(queries):
            if not isinstance(item, dict):
                continue
            artifact_id = self._normalize_text(item.get("id")) or f"special_query_{index}"
            description = self._normalize_text(item.get("description"))
            example_queries = [
                self._normalize_text(example)
                for example in (item.get("example_queries") or [])
                if self._normalize_text(example)
            ]
            candidate_tables = self._dedupe_tables(item.get("candidate_tables") or item.get("required_tables") or [])
            if not candidate_tables:
                candidate_tables = self._candidate_tables_from_text(json.dumps(item, sort_keys=True), available_tables)
            text_parts = [
                f"special query {artifact_id}",
                f"description {description}" if description else "",
                f"examples {'; '.join(example_queries)}" if example_queries else "",
            ]
            artifacts.append(
                {
                    "kind": "special_query",
                    "artifact_id": artifact_id,
                    "text": " | ".join(part for part in text_parts if part),
                    "candidate_tables": candidate_tables,
                    "route": self._normalize_text(item.get("route")).upper() or "SQL",
                }
            )
        return artifacts

    def _report_artifacts(self, domain: Any) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        domain_path = Path(getattr(domain, "domain_path", "") or "")
        reports_path = domain_path / "reports.json"
        if not reports_path.exists():
            return artifacts
        try:
            payload = json.loads(reports_path.read_text())
        except Exception:
            logger.warning("Failed to load reports.json for semantic retrieval at %s", reports_path, exc_info=True)
            return artifacts

        reports = payload.get("reports") if isinstance(payload, dict) else {}
        if not isinstance(reports, dict):
            return artifacts

        for report_id, config in sorted(reports.items()):
            if not isinstance(config, dict):
                continue
            report_name = self._normalize_text(config.get("name"))
            aliases = [self._normalize_text(alias) for alias in (config.get("aliases") or []) if self._normalize_text(alias)]
            description = self._normalize_text(config.get("description"))
            text_parts = [
                f"report {self._normalize_text(report_id)}",
                f"name {report_name}" if report_name else "",
                f"aliases {'; '.join(aliases)}" if aliases else "",
                f"description {description}" if description else "",
            ]
            artifacts.append(
                {
                    "kind": "report",
                    "artifact_id": self._normalize_text(report_id),
                    "text": " | ".join(part for part in text_parts if part),
                    "candidate_tables": [],
                    "route": "REPORT",
                }
            )
        return artifacts

    def _knowledge_artifacts(self, domain: Any, available_tables: Set[str]) -> List[Dict[str, Any]]:
        getter = getattr(domain, "get_domain_knowledge_config", None)
        payload = getter() if callable(getter) else {}
        knowledge = dict(payload) if isinstance(payload, dict) else {}
        artifacts: List[Dict[str, Any]] = []

        example_queries = [
            self._normalize_text(query)
            for query in (knowledge.get("example_queries") or [])
            if self._normalize_text(query)
        ]
        for index, query in enumerate(example_queries):
            artifacts.append(
                {
                    "kind": "knowledge",
                    "artifact_id": f"knowledge_query_{index}",
                    "text": f"domain example query {query}",
                    "candidate_tables": self._candidate_tables_from_text(query, available_tables),
                    "route": "SQL",
                }
            )

        relationships = knowledge.get("business_relationships") if isinstance(knowledge.get("business_relationships"), dict) else {}
        for key, value in sorted(relationships.items()):
            key_text = self._normalize_text(key)
            value_text = self._normalize_text(value)
            if not key_text or not value_text:
                continue
            artifacts.append(
                {
                    "kind": "knowledge",
                    "artifact_id": key_text,
                    "text": f"business relationship {key_text}: {value_text}",
                    "candidate_tables": self._candidate_tables_from_text(value_text, available_tables),
                    "route": "SQL",
                }
            )
        return artifacts

    def _build_artifacts(self) -> List[Dict[str, Any]]:
        bundle_artifacts = self._chunker.build_chunks()
        if bundle_artifacts:
            return [artifact for artifact in bundle_artifacts if self._normalize_text(artifact.get("text"))]

        domain = self._domain()
        available_tables = self._available_tables()
        artifacts: List[Dict[str, Any]] = []
        artifacts.extend(self._table_artifacts(domain, available_tables))
        artifacts.extend(self._example_artifacts(domain, available_tables))
        artifacts.extend(self._glossary_artifacts(domain, available_tables))
        artifacts.extend(self._special_query_artifacts(domain, available_tables))
        artifacts.extend(self._report_artifacts(domain))
        artifacts.extend(self._knowledge_artifacts(domain, available_tables))
        return [artifact for artifact in artifacts if self._normalize_text(artifact.get("text"))]

    def _artifacts(self) -> List[Dict[str, Any]]:
        cache_key = self._domain_key()
        cached = self._artifact_cache.get(cache_key)
        if isinstance(cached, list) and cached:
            return [dict(item) for item in cached]
        artifacts = self._build_artifacts()
        self._artifact_cache[cache_key] = [dict(item) for item in artifacts]
        return artifacts

    def _vectors(self) -> List[List[float]]:
        cache_key = self._domain_key()
        cached = self._vector_cache.get(cache_key)
        if isinstance(cached, list) and cached:
            return [list(item) for item in cached]
        artifacts = self._artifacts()
        embedder = self._ensure_embedder()
        vectors = embedder.embed([artifact["text"] for artifact in artifacts])
        self._vector_cache[cache_key] = [list(vector) for vector in vectors]
        return vectors

    def search(
        self,
        query: str,
        *,
        kinds: Optional[Set[str]] = None,
        limit: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        if not self.is_enabled():
            return []
        query_text = self._normalize_text(query)
        if not query_text:
            return []

        if self.provider == "chroma" and self._chroma_available():
            store = self._chroma_store
            try:
                cache_key = self._domain_key()
                if cache_key not in self._indexed_domains and hasattr(store, "reindex_domain"):
                    if int(store.reindex_domain() or 0) > 0:
                        self._indexed_domains.add(cache_key)
                hits = store.search(
                    query_text,
                    kinds=kinds,
                    limit=max(1, int(limit or self.default_top_k)),
                    min_score=self.default_min_score if min_score is None else float(min_score),
                )
                if hits:
                    return hits
            except Exception as exc:
                logger.warning("Chroma semantic retrieval unavailable, falling back to fastembed: %s", exc)

        try:
            artifacts = self._artifacts()
            vectors = self._vectors()
            embedder = self._ensure_embedder()
            query_vector = embedder.embed([query_text])[0]
        except Exception as exc:
            logger.warning("Semantic retrieval unavailable, using lexical fallback only: %s", exc)
            return []

        allowed_kinds = {str(kind).strip().lower() for kind in (kinds or set()) if str(kind).strip()}
        threshold = self.default_min_score if min_score is None else float(min_score)
        max_hits = max(1, int(limit or self.default_top_k))
        scored: List[Dict[str, Any]] = []

        for artifact, vector in zip(artifacts, vectors):
            kind = str(artifact.get("kind", "")).strip().lower()
            if allowed_kinds and kind not in allowed_kinds:
                continue
            score = self._cosine(query_vector, vector)
            if score < threshold:
                continue
            scored.append(
                {
                    **artifact,
                    "score": float(score),
                }
            )

        scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return scored[:max_hits]

    def reindex(self) -> int:
        if not self._chroma_available():
            return 0
        count = int(self._chroma_store.reindex_domain() or 0)
        if count > 0:
            self._indexed_domains.add(self._domain_key())
        return count

    def warmup(self) -> Dict[str, int]:
        if not self.is_enabled():
            return {"artifacts": 0, "indexed": 0}

        artifacts = self._artifacts()
        artifact_count = len(artifacts)

        if self.provider == "chroma" and self._chroma_available():
            indexed_count = int(self._chroma_store.reindex_domain() or 0)
            if indexed_count > 0:
                self._indexed_domains.add(self._domain_key())
            return {"artifacts": artifact_count, "indexed": indexed_count}

        if artifact_count > 0:
            self._vectors()

        return {"artifacts": artifact_count, "indexed": artifact_count}

    def remember_success(
        self,
        *,
        question: str,
        sql: str,
        candidate_tables: Optional[Sequence[str]] = None,
    ) -> str:
        if not self._chroma_available():
            return ""
        try:
            return str(
                self._chroma_store.remember_success(
                    question=question,
                    sql=sql,
                    candidate_tables=candidate_tables,
                )
                or ""
            ).strip()
        except Exception as exc:
            logger.warning("Failed to store semantic learned query: %s", exc)
            return ""

    def render_hits(
        self,
        hits: Sequence[Dict[str, Any]],
        *,
        limit: Optional[int] = None,
    ) -> str:
        rendered: List[str] = []
        max_items = max(1, int(limit or self.default_prompt_k))
        for hit in list(hits)[:max_items]:
            kind = self._normalize_text(hit.get("kind")).replace("_", " ")
            artifact_id = self._normalize_text(hit.get("artifact_id"))
            text = self._normalize_text(hit.get("text"))
            score = float(hit.get("score") or 0.0)
            if not text:
                continue
            rendered.append(f"- [{kind}] {artifact_id} (score={score:.2f}): {text}")
        return "\n".join(rendered)
