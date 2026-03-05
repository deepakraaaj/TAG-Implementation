"""Domain registry for loading and managing domain-specific configuration."""
import json
import logging
import os
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class DomainRegistry:
    """
    Central registry for domain-specific configuration.
    Loads configuration from app/domains/{DOMAIN}/ folder.
    """

    _instance: Optional["DomainRegistry"] = None
    _domain_name: str = ""
    _config: Dict[str, Any] = {}
    _manifest: Dict[str, Any] = {}
    _enums_module: Any = None
    _fields_module: Any = None
    _rules_module: Any = None
    _fallback_domain_name: str = "starter"

    def __init__(self, domain_name: Optional[str] = None):
        """Initialize domain registry with specified domain."""
        self._domain_name = domain_name or os.getenv("DOMAIN", "maintenance")
        self._load_domain()

    @classmethod
    def get_current_domain(cls) -> "DomainRegistry":
        """Get singleton instance of current domain."""
        domain_name = os.getenv("DOMAIN", "maintenance")
        if cls._instance is None or cls._instance._domain_name != domain_name:
            cls._instance = cls(domain_name)
        return cls._instance

    def _load_domain(self) -> None:
        """Load all domain configuration files."""
        domains_root = Path(__file__).parent
        requested_domain = str(self._domain_name or "").strip()
        requested_path = domains_root / requested_domain

        fallback_domain = str(self._fallback_domain_name or "").strip() or "starter"
        fallback_path = domains_root / fallback_domain
        if not fallback_path.exists():
            fallback_domain = "maintenance"
            fallback_path = domains_root / fallback_domain

        active_domain = requested_domain
        active_path = requested_path
        if not requested_path.exists():
            if fallback_path.exists():
                logger.warning(
                    "Domain '%s' not found at %s. Falling back to '%s'.",
                    requested_domain,
                    requested_path,
                    fallback_domain,
                )
                active_domain = fallback_domain
                active_path = fallback_path
            else:
                raise ValueError(f"Domain '{requested_domain}' not found at {requested_path}")

        self._domain_name = active_domain

        base_config = self._load_json_dict((fallback_path / "domain.json") if fallback_path.exists() else None)
        base_manifest = self._load_json_dict((fallback_path / "schema_manifest.json") if fallback_path.exists() else None)
        active_config = self._load_json_dict(active_path / "domain.json")
        active_manifest = self._load_json_dict(active_path / "schema_manifest.json")

        self._config = self._deep_merge_dicts(base_config, active_config)
        self._manifest = self._merge_manifest(base_manifest, active_manifest)

        fallback_prefix = f"app.domains.{fallback_domain}" if fallback_path.exists() else ""
        active_prefix = f"app.domains.{active_domain}"
        self._enums_module = self._import_optional_module(
            f"{active_prefix}.enums",
            fallback_module=(f"{fallback_prefix}.enums" if fallback_prefix else ""),
            default_attrs={"ENUM_MAPPINGS": {}, "ENUM_LABELS": {}},
        )
        self._fields_module = self._import_optional_module(
            f"{active_prefix}.fields",
            fallback_module=(f"{fallback_prefix}.fields" if fallback_prefix else ""),
            default_attrs={"FIELD_LABELS": {}, "FIELD_OPTIONS": {}, "LOOKUP_CONFIGS": {}},
        )
        self._rules_module = self._import_optional_module(
            f"{active_prefix}.rules",
            fallback_module=(f"{fallback_prefix}.rules" if fallback_prefix else ""),
            default_attrs={},
        )

    @staticmethod
    def _load_json_dict(path: Optional[Path]) -> Dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                payload = json.load(f)
                return dict(payload) if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load JSON config at %s: %s", path, exc)
            return {}

    @staticmethod
    def _deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(base or {})
        for key, value in (override or {}).items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = DomainRegistry._deep_merge_dicts(out[key], value)
            else:
                out[key] = value
        return out

    @staticmethod
    def _merge_manifest(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge manifest config while avoiding fallback table/template leakage across domains.
        Domain-specific manifest sections should replace fallback content when explicitly provided.
        """
        merged = DomainRegistry._deep_merge_dicts(base, override)
        for key in ("tables", "query_templates", "table_resolution_rules"):
            if key not in (override or {}):
                continue
            value = override.get(key)
            if isinstance(value, dict):
                merged[key] = dict(value)
            elif isinstance(value, list):
                merged[key] = list(value)
        return merged

    @staticmethod
    def _import_optional_module(
        module_name: str,
        fallback_module: str = "",
        default_attrs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        import importlib

        attrs = dict(default_attrs or {})
        for candidate in (module_name, fallback_module):
            if not candidate:
                continue
            try:
                return importlib.import_module(candidate)
            except Exception as exc:
                logger.warning("Unable to import module '%s': %s", candidate, exc)
                continue
        return types.SimpleNamespace(**attrs)

    @property
    def name(self) -> str:
        """Get domain name."""
        return self._domain_name

    @property
    def description(self) -> str:
        """Get domain description."""
        return self._config.get("description", "")

    @property
    def domain_path(self) -> Path:
        """Get absolute path for the active domain package directory."""
        return Path(__file__).parent / self._domain_name

    @property
    def manifest(self) -> Dict[str, Any]:
        """Get schema manifest."""
        return self._manifest

    @property
    def config(self) -> Dict[str, Any]:
        """Get domain configuration."""
        return self._config

    def get_enum_mapping(self, column: str, value: Any) -> Any:
        """
        Get enum integer value for a column.

        Args:
            column: Column name (e.g., 'status')
            value: String value (e.g., 'pending')

        Returns:
            Integer value or original value if no mapping exists
        """
        if not self._enums_module:
            return value

        mappings = getattr(self._enums_module, "ENUM_MAPPINGS", {})
        column_map = mappings.get(column.lower(), {})

        if not column_map:
            return value

        # Normalize value for lookup
        normalized = str(value).strip().lower().replace(" ", "").replace("_", "")
        return column_map.get(normalized, value)

    def get_enum_label(self, column: str, value: Any) -> Any:
        """
        Get enum label for an integer value.

        Args:
            column: Column name (e.g., 'status')
            value: Integer value (e.g., 0)

        Returns:
            Label string or original value if no mapping exists
        """
        if not self._enums_module:
            return value

        labels = getattr(self._enums_module, "ENUM_LABELS", {})
        column_labels = labels.get(column.lower(), {})

        if isinstance(value, int) and value in column_labels:
            return column_labels[value]

        return value

    def enum_columns(self) -> set[str]:
        """Return domain enum-mapped column names."""
        mappings = getattr(self._enums_module, "ENUM_MAPPINGS", {}) if self._enums_module else {}
        if not isinstance(mappings, dict):
            return set()
        return {str(col or "").strip().lower() for col in mappings.keys() if str(col or "").strip()}

    def get_field_label(self, field_name: str) -> str:
        """Get human-readable label for a field."""
        if not self._fields_module:
            return field_name

        labels = getattr(self._fields_module, "FIELD_LABELS", {})
        return labels.get(field_name, field_name)

    def get_field_options(self, field_name: str) -> List[Dict[str, str]]:
        """Get dropdown options for a field."""
        if not self._fields_module:
            return []

        options = getattr(self._fields_module, "FIELD_OPTIONS", {})
        return options.get(field_name, [])

    def get_lookup_config(self, field_name: str) -> Dict[str, Any]:
        """Get lookup table configuration for a field."""
        if not self._fields_module:
            return {}

        configs = getattr(self._fields_module, "LOOKUP_CONFIGS", {})
        return configs.get(field_name, {})

    def apply_conditional_fields(
        self, table: str, required_fields: List[str], collected_fields: Dict[str, Any]
    ) -> List[str]:
        """Apply domain-specific field visibility rules."""
        if not self._rules_module:
            return required_fields

        func = getattr(self._rules_module, "apply_conditional_fields", None)
        if func:
            return func(table, required_fields, collected_fields)

        return required_fields

    def is_flow_candidate(self, message: str, table: str) -> bool:
        """Determine if message should trigger a flow."""
        if not self._rules_module:
            return False

        func = getattr(self._rules_module, "is_flow_candidate", None)
        if func:
            return func(message, table)

        return False

    def get_capabilities(self) -> Dict[str, Any]:
        """Get domain capabilities for help/discovery."""
        return self._config.get("capabilities", {})

    def get_config_section(self, section: str) -> Dict[str, Any]:
        """Get a top-level domain config section as a dict."""
        key = str(section or "").strip()
        if not key:
            return {}
        payload = self._config.get(key, {})
        return dict(payload) if isinstance(payload, dict) else {}

    def get_assistant_prompt_config(self) -> Dict[str, Any]:
        return self.get_config_section("assistant_prompt")

    def get_intent_detection_config(self) -> Dict[str, Any]:
        return self.get_config_section("intent_detection")

    def get_entity_behavior_config(self) -> Dict[str, Any]:
        return self.get_config_section("entity_behavior")

    def get_user_lookup_config(self) -> Dict[str, Any]:
        return self.get_config_section("user_lookup")

    def get_response_messages(self) -> Dict[str, str]:
        payload = self.get_config_section("response_messages")
        normalized: Dict[str, str] = {}
        for key, value in payload.items():
            k = str(key or "").strip()
            if not k:
                continue
            normalized[k] = str(value or "").strip()
        return normalized

    def get_response_message(self, key: str, default: str = "") -> str:
        msg = str(self.get_response_messages().get(str(key or "").strip(), "")).strip()
        return msg or str(default or "")

    def format_no_records_message(self, sql: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Optional domain hook for no-record messaging.
        The domain `rules.py` can expose `format_no_records_message(context: Dict) -> str`.
        """
        if not self._rules_module:
            return ""
        formatter = getattr(self._rules_module, "format_no_records_message", None)
        if not callable(formatter):
            return ""
        try:
            context = {
                "sql": str(sql or ""),
                "metadata": dict(metadata or {}),
                "response_messages": self.get_response_messages(),
            }
            message = formatter(context)
            return str(message or "").strip()
        except Exception:
            return ""

    def get_flow_path(self, flow_name: str) -> Optional[Path]:
        """Get path to a flow YAML file."""
        domain_path = Path(__file__).parent / self._domain_name / "flows"
        flow_file = domain_path / f"{flow_name}.yaml"
        return flow_file if flow_file.exists() else None
