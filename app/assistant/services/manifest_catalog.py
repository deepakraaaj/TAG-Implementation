import re
from typing import Any, Dict, List, Set

from app.services.schema_manifest_service import SchemaManifestService


class ManifestCatalog:
    def __init__(self):
        self.manifest = SchemaManifestService().manifest

    def table_names(self) -> Set[str]:
        return set((self.manifest.get("tables") or {}).keys())

    def table_meta(self, table: str) -> Dict[str, Any]:
        return (self.manifest.get("tables") or {}).get(table, {}) or {}

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
    def _contains_term(query: str, term: str) -> bool:
        t = str(term or "").strip().lower()
        if not t:
            return False
        if " " in t:
            return t in query
        return bool(re.search(rf"\b{re.escape(t)}\b", query))

    def _rule_matches(self, rule: Dict[str, Any], query: str) -> bool:
        all_terms = [str(x).strip() for x in (rule.get("all_terms") or []) if str(x).strip()]
        any_terms = [str(x).strip() for x in (rule.get("any_terms") or []) if str(x).strip()]

        if all_terms and not all(self._contains_term(query, term) for term in all_terms):
            return False
        if any_terms and not any(self._contains_term(query, term) for term in any_terms):
            return False
        return bool(all_terms or any_terms)

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

        best_table = ""
        best_score = -1
        for table in sorted(self.table_names()):
            aliases = self.aliases(table)
            for alias in aliases:
                a = str(alias or "").strip().lower()
                if not a:
                    continue
                if self._contains_term(q, a):
                    # Longer alias = more specific match.
                    score = len(a)
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
