import re
from typing import Any, Dict, List


class QueryPolicyService:
    def __init__(self, domain: Any):
        self.domain = domain

    def raw(self) -> Dict[str, Any]:
        domain_cfg = getattr(self.domain, "config", {})
        if not isinstance(domain_cfg, dict):
            return {}
        policy = domain_cfg.get("query_policy")
        return policy if isinstance(policy, dict) else {}

    def get_list(self, key: str, default: List[str]) -> List[str]:
        value = self.raw().get(key)
        if isinstance(value, list):
            out = [str(x).strip() for x in value if str(x).strip()]
            if out:
                return out
        return list(default)

    def get_str(self, key: str, default: str) -> str:
        value = self.raw().get(key)
        text_value = str(value or "").strip()
        return text_value or default

    @staticmethod
    def safe_identifier(name: str) -> str:
        text = str(name or "").strip()
        return text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else ""

    def tenant_column(self) -> str:
        ident = self.safe_identifier(self.get_str("tenant_column", "company_id"))
        return ident or "company_id"

    def tenant_metadata_value(self, metadata: Dict[str, Any]) -> Any:
        keys = self.get_list("tenant_metadata_keys", ["company_id"])
        for key in keys:
            if key in (metadata or {}) and (metadata or {}).get(key) is not None:
                return (metadata or {}).get(key)
        return (metadata or {}).get("company_id")

    def task_table_name(self, table_names: List[str] | None = None) -> str:
        configured = self.safe_identifier(self.get_str("task_table_name", ""))
        if configured:
            return configured
        for name in [str(t).strip() for t in (table_names or []) if str(t).strip()]:
            if "task" in name.lower():
                return name
        return ""

    def system_columns(self) -> set[str]:
        tenant_col = self.tenant_column()
        default_cols = ["id", tenant_col, "created_by", "updated_by", "date_created", "date_updated"]
        return set(self.get_list("system_columns", default_cols))
