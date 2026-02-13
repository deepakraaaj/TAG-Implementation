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

    def resolve_table_from_query(self, query: str) -> str:
        q = (query or "").lower()
        if not q:
            return ""
        for table in self.table_names():
            aliases = self.aliases(table)
            if any(alias in q for alias in aliases):
                return table
        return ""

    def required_create_fields(self, table: str) -> List[str]:
        create_cfg = ((self.table_meta(table).get("operations") or {}).get("create") or {})
        return [str(x).strip() for x in create_cfg.get("required_fields", []) if str(x).strip()]
