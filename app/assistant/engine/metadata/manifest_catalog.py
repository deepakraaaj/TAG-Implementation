import re
from typing import Any, Callable, Dict, List, Optional, Set

from app.services.data.schema_autodiscovery_service import SchemaAutoDiscoveryService


class ManifestCatalog:
    def __init__(
        self,
        domain_provider: Callable[[], Any],
        schema_service: Any = None,
        db_url: str | None = None,
        semantic_retriever: Any = None,
    ):
        self.domain_provider = domain_provider
        self._schema_service = schema_service
        self._db_url = db_url
        self.semantic_retriever = semantic_retriever
        self._autodiscovery: Optional[SchemaAutoDiscoveryService] = None
        if schema_service is not None:
            self._autodiscovery = SchemaAutoDiscoveryService(schema_service)

    @property
    def manifest(self) -> Dict[str, Any]:
        domain = self.domain_provider()
        payload = getattr(domain, "manifest", {})
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _autodiscovered_manifest(self) -> Dict[str, Any]:
        """Returns a synthetic manifest built from live DB introspection."""
        if self._autodiscovery is None:
            return {}
        return self._autodiscovery.build_manifest(self._db_url)

    def _effective_tables(self) -> Dict[str, Any]:
        """Return manifest tables, falling back to auto-discovered tables."""
        tables = self.manifest.get("tables") or {}
        if tables:
            return tables
        return self._autodiscovered_manifest().get("tables") or {}

    def table_names(self) -> Set[str]:
        return set(self._effective_tables().keys())

    def table_meta(self, table: str) -> Dict[str, Any]:
        return self._effective_tables().get(table, {}) or {}

    def important_columns(self, table: str) -> Set[str]:
        return set((self.table_meta(table).get("important_columns") or {}).keys())

    def aliases(self, table: str) -> List[str]:
        meta = self.table_meta(table)
        base = [table.lower()]
        base.extend(str(a).lower() for a in (meta.get("aliases") or []) if str(a).strip())
        return list(dict.fromkeys(base))

    def table_resolution_rules(self) -> List[Dict[str, Any]]:
        rules = self.manifest.get("table_resolution_rules") or []
        return [dict(x) for x in rules if isinstance(x, dict)]

    @staticmethod
    def _normalize_search_text(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").strip().lower()).strip()

    @classmethod
    def _term_variants(cls, term: str) -> List[str]:
        normalized = cls._normalize_search_text(term)
        if not normalized:
            return []

        variants = [normalized]
        parts = normalized.split()
        if not parts:
            return variants

        last = parts[-1]
        plural_last = ""
        if last.endswith("y") and len(last) > 1 and last[-2] not in "aeiou":
            plural_last = last[:-1] + "ies"
        elif re.search(r"(s|x|z|ch|sh)$", last):
            plural_last = last + "es"
        elif not last.endswith("s"):
            plural_last = last + "s"

        if plural_last:
            variants.append(" ".join(parts[:-1] + [plural_last]))

        return list(dict.fromkeys(variants))

    @classmethod
    def _term_match(cls, query: str, term: str) -> tuple[int, int]:
        normalized_query = cls._normalize_search_text(query)
        if not normalized_query:
            return (-1, 0)

        best = (-1, 0)
        for candidate in cls._term_variants(term):
            match = re.search(rf"\b{re.escape(candidate)}\b", normalized_query)
            if not match:
                continue
            score = (match.start(), len(candidate))
            if score[1] > best[1] or (score[1] == best[1] and (best[0] == -1 or score[0] < best[0])):
                best = score
        return best

    @classmethod
    def _contains_term(cls, query: str, term: str) -> bool:
        return cls._term_match(query, term)[0] >= 0

    @staticmethod
    def _is_mapping_query(query: str) -> bool:
        q = str(query or "").strip().lower()
        if not q:
            return False
        return bool(re.search(r"\b(map|maps|mapped|mapping|linked|associated)\b", q))

    def _mapping_table_match_score(self, query: str, table: str) -> tuple[int, int]:
        q = str(query or "").strip().lower()
        if not q or not self._is_mapping_query(q):
            return (0, 0)

        meta = self.table_meta(table)
        joins = meta.get("joins") if isinstance(meta, dict) else {}
        if not isinstance(joins, dict) or not joins:
            return (0, 0)

        matched_entities = 0
        specificity = 0
        for joined_table in joins.keys():
            target = str(joined_table or "").strip()
            if not target:
                continue
            labels = self.aliases(target) if target in self.table_names() else [target]
            matched_lengths = [
                len(str(label or "").strip())
                for label in labels
                if self._contains_term(q, label)
            ]
            if matched_lengths:
                matched_entities += 1
                specificity += max(matched_lengths)

        if matched_entities < 2:
            return (0, 0)
        return (matched_entities, specificity)

    def _rule_matches(self, rule: Dict[str, Any], query: str) -> bool:
        all_terms = [str(x).strip() for x in (rule.get("all_terms") or []) if str(x).strip()]
        any_terms = [str(x).strip() for x in (rule.get("any_terms") or []) if str(x).strip()]

        if all_terms and not all(self._contains_term(query, term) for term in all_terms):
            return False
        if any_terms and not any(self._contains_term(query, term) for term in any_terms):
            return False
        return bool(all_terms or any_terms)

    def get_candidate_tables(self, query: str, limit: int = 15) -> Dict[str, Any]:
        """
        Return up to `limit` tables from the manifest that are relevant to the query.
        """
        q = (query or "").lower()
        if not q:
            # Fallback to first N tables if no query (e.g. initial greeting)
            return dict(list(self._effective_tables().items())[:limit])

        # 1. Exact matches / specific resolution rules first
        resolved = self.resolve_table_from_query(q)
        candidates: Dict[str, Any] = {}
        if resolved:
            candidates[resolved] = self.table_meta(resolved)

        # 2. Semantic artifact retrieval can surface relevant tables even when
        # the user does not mention exact aliases.
        retriever = getattr(self, "semantic_retriever", None)
        if retriever is not None and hasattr(retriever, "search"):
            try:
                hits = retriever.search(
                    q,
                    kinds={"table", "example", "special_query", "knowledge", "term"},
                    limit=min(limit, 8),
                )
            except Exception:
                hits = []
            for hit in hits:
                for table_name in hit.get("candidate_tables") or []:
                    table_key = str(table_name or "").strip()
                    if table_key and table_key in self._effective_tables() and table_key not in candidates:
                        candidates[table_key] = self.table_meta(table_key)

        # 3. Heuristic search: check names, aliases, and descriptions
        all_tables = self._effective_tables()
        for table, meta in all_tables.items():
            if table in candidates:
                continue
            
            # Check table name
            if self._contains_term(q, table):
                candidates[table] = meta
                continue
            
            # Check aliases
            aliases = self.aliases(table)
            if any(self._contains_term(q, a) for a in aliases):
                candidates[table] = meta
                continue
                
            # Check descriptions
            desc = str(meta.get("description") or "").lower()
            if any(word in q for word in desc.split() if len(word) > 3):
                candidates[table] = meta
                continue

        # 4. If we still have space, add the primary table if it exists
        primary_table = self.manifest.get("primary_table")
        if primary_table and primary_table not in candidates and len(candidates) < limit:
            candidates[primary_table] = self.table_meta(primary_table)

        # 5. Fill remaining slots with alphabetical fallback (to ensure LLM sees *something*)
        if len(candidates) < limit:
            for table in sorted(all_tables.keys()):
                if table not in candidates:
                    candidates[table] = all_tables[table]
                if len(candidates) >= limit:
                    break

        return candidates

    def resolve_table_from_query(self, query: str) -> str:
        q = (query or "").lower()
        if not q:
            return ""

        # Manifest-driven priority rules for disambiguation.
        for rule in sorted(
            self.table_resolution_rules(),
            key=lambda item: int(item.get("priority", 0) or 0),
            reverse=True,
        ):
            target_table = str(rule.get("target_table", "")).strip()
            if not target_table or target_table not in self.table_names():
                continue
            if self._rule_matches(rule, q):
                return target_table

        if self._is_mapping_query(q):
            best_mapping_table = ""
            best_mapping_score = (0, 0)
            for table in sorted(self.table_names()):
                score = self._mapping_table_match_score(q, table)
                if score > best_mapping_score:
                    best_mapping_score = score
                    best_mapping_table = table
            if best_mapping_table:
                return best_mapping_table

        best_table = ""
        best_score = (0, float("-inf"))
        for table in sorted(self.table_names()):
            aliases = self.aliases(table)
            for alias in aliases:
                position, matched_length = self._term_match(q, alias)
                if position < 0:
                    continue
                # Prefer more specific matches, then earlier mentions in the query.
                score = (matched_length, -position)
                if score > best_score:
                    best_score = score
                    best_table = table

        if best_table:
            return best_table
        return ""

    def required_create_fields(self, table: str) -> List[str]:
        create_cfg = ((self.table_meta(table).get("operations") or {}).get("create") or {})
        return [str(x).strip() for x in create_cfg.get("required_fields", []) if str(x).strip()]

    def create_enabled(self, table: str) -> bool:
        create_cfg = ((self.table_meta(table).get("operations") or {}).get("create") or {})
        return bool(create_cfg.get("enabled", False))

    def get_query_template(self, table: str, template_type: str = "list") -> str:
        templates = (self.manifest.get("query_templates") or {}).get(table, {})
        return str(templates.get(template_type, "")).strip()
