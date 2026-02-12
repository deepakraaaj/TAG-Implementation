import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastembed import TextEmbedding

logger = logging.getLogger(__name__)


class SchemaManifestService:
    def __init__(self, manifest_path: Optional[Path] = None):
        self.manifest_path = manifest_path or Path(__file__).with_name("schema_manifest.json")
        self._manifest = self._load_manifest()
        self._embedder = None

    def _load_manifest(self) -> Dict:
        if not self.manifest_path.exists():
            logger.warning("Schema manifest not found at %s", self.manifest_path)
            return {"tables": {}, "few_shot_examples": []}
        try:
            return json.loads(self.manifest_path.read_text())
        except Exception as e:
            logger.error("Failed to load schema manifest: %s", e)
            return {"tables": {}, "few_shot_examples": []}

    @property
    def manifest(self) -> Dict:
        return self._manifest

    def _aliases_for_table(self, table_name: str) -> List[str]:
        table_meta = self._manifest.get("tables", {}).get(table_name, {})
        aliases = [table_name.lower()]
        custom_aliases = table_meta.get("aliases", [])
        if isinstance(custom_aliases, list):
            aliases.extend([str(a).strip().lower() for a in custom_aliases if str(a).strip()])
        return list(dict.fromkeys(aliases))

    @staticmethod
    def _contains_alias(query: str, alias: str) -> bool:
        escaped = re.escape(alias.strip().lower())
        if not escaped:
            return False
        pattern = rf"(?<!\w){escaped}(?!\w)"
        return bool(re.search(pattern, query))

    def resolve_entity_table(self, query: str, intent_analysis: Dict[str, Any]) -> Optional[str]:
        q = (query or "").strip().lower()
        if not q:
            return None

        entities = [str(e).strip().lower() for e in intent_analysis.get("entities", []) if str(e).strip()]
        tables_meta = self._manifest.get("tables", {})
        if not isinstance(tables_meta, dict):
            return None

        # Prefer explicit entity resolution from intent output.
        for table_name in tables_meta.keys():
            aliases = self._aliases_for_table(table_name)
            if any(entity in aliases for entity in entities):
                return table_name

        # Fallback to lexical alias match from user query.
        for table_name in tables_meta.keys():
            aliases = self._aliases_for_table(table_name)
            if any(self._contains_alias(q, alias) for alias in aliases):
                return table_name

        return None

    def render_query_template(self, table_name: str, template_kind: str, **kwargs: Any) -> str:
        templates = self._manifest.get("query_templates", {})
        if not isinstance(templates, dict):
            return ""
        table_templates = templates.get(table_name, {})
        if not isinstance(table_templates, dict):
            return ""
        template = table_templates.get(template_kind, "")
        if not template or not isinstance(template, str):
            return ""
        try:
            return template.format(**kwargs)
        except Exception as e:
            logger.warning("Failed to render query template for %s.%s: %s", table_name, template_kind, e)
            return ""

    def _table_doc(self, table_name: str) -> str:
        table_meta = self._manifest.get("tables", {}).get(table_name, {})
        parts = [f"table: {table_name}"]
        if table_meta.get("description"):
            parts.append(f"description: {table_meta['description']}")
        for col, info in table_meta.get("important_columns", {}).items():
            cdesc = info.get("description", "")
            parts.append(f"column: {col} {cdesc}".strip())
        return " | ".join(parts)

    def _ensure_embedder(self):
        if self._embedder is None:
            self._embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        return self._embedder

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))

    def semantic_select_tables(self, query: str, all_tables: List[str], top_k: int = 5) -> List[str]:
        if not all_tables:
            return []
        candidates = list(dict.fromkeys(all_tables))

        try:
            embedder = self._ensure_embedder()
            docs = [self._table_doc(t) for t in candidates]
            vectors = [vec.tolist() for vec in embedder.embed(docs)]
            query_vec = list(embedder.embed([query]))[0].tolist()
            scored: List[Tuple[str, float]] = []
            for table, vec in zip(candidates, vectors):
                scored.append((table, self._cosine(query_vec, vec)))
            scored.sort(key=lambda x: x[1], reverse=True)
            top_tables = [table for table, _ in scored[: max(1, min(top_k, len(scored)))]]
            return top_tables
        except Exception as e:
            logger.warning("Semantic table selection failed, falling back to lexical: %s", e)
            lower_query = (query or "").lower()
            lexical = [t for t in candidates if t.lower() in lower_query]
            return lexical[:top_k] if lexical else candidates[: min(3, len(candidates))]

    def render_manifest_context(self, selected_tables: List[str]) -> str:
        lines: List[str] = []
        tables_meta = self._manifest.get("tables", {})
        for table in selected_tables:
            meta = tables_meta.get(table, {})
            if not meta:
                continue
            desc = meta.get("description", "")
            if desc:
                lines.append(f"- {table}: {desc}")
            for col, info in meta.get("important_columns", {}).items():
                cdesc = info.get("description", "")
                if cdesc:
                    lines.append(f"  - {table}.{col}: {cdesc}")
        return "\n".join(lines)

    def render_join_hints(self, selected_tables: List[str]) -> str:
        lines: List[str] = []
        tables_meta = self._manifest.get("tables", {})
        selected = set(selected_tables)
        for left in selected_tables:
            joins = tables_meta.get(left, {}).get("joins", {})
            for right, cond in joins.items():
                if right in selected:
                    lines.append(f"- {left} -> {right} on {cond}")
        return "\n".join(lines)

    def render_few_shot_examples(self, intent_type: str = "") -> str:
        rows = self._manifest.get("few_shot_examples", [])
        selected = rows
        if intent_type:
            selected = [r for r in rows if r.get("intent_type", "").lower() == intent_type.lower()] or rows
        lines: List[str] = []
        for row in selected[:2]:
            lines.append(f"Q: {row.get('question', '')}")
            lines.append(f"SQL: {row.get('sql', '')}")
        return "\n".join(lines)
