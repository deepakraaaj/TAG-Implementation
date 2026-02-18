"""Domain registry for loading and managing domain-specific configuration."""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings

settings = get_settings()


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
        domain_path = Path(__file__).parent / self._domain_name

        if not domain_path.exists():
            raise ValueError(f"Domain '{self._domain_name}' not found at {domain_path}")

        # Load domain.json
        config_file = domain_path / "domain.json"
        if config_file.exists():
            with open(config_file, "r") as f:
                self._config = json.load(f)

        # Load schema_manifest.json
        manifest_file = domain_path / "schema_manifest.json"
        if manifest_file.exists():
            with open(manifest_file, "r") as f:
                self._manifest = json.load(f)

        # Dynamically import domain modules
        domain_module = f"app.domains.{self._domain_name}"
        try:
            import importlib

            self._enums_module = importlib.import_module(f"{domain_module}.enums")
            self._fields_module = importlib.import_module(f"{domain_module}.fields")
            self._rules_module = importlib.import_module(f"{domain_module}.rules")
        except ImportError as e:
            raise ValueError(f"Failed to load domain modules for '{self._domain_name}': {e}")

    @property
    def name(self) -> str:
        """Get domain name."""
        return self._domain_name

    @property
    def description(self) -> str:
        """Get domain description."""
        return self._config.get("description", "")

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

    def get_flow_path(self, flow_name: str) -> Optional[Path]:
        """Get path to a flow YAML file."""
        domain_path = Path(__file__).parent / self._domain_name / "flows"
        flow_file = domain_path / f"{flow_name}.yaml"
        return flow_file if flow_file.exists() else None
