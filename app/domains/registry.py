"""Domain registry for loading and managing domain-specific configuration."""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.config import get_settings
from app.domains.config_models import DomainConfigModel, DomainManifestModel, DomainSpec

logger = logging.getLogger(__name__)

_CRITICAL_CONFIG_CONFLICT_PATHS: tuple[tuple[str, ...], ...] = (
    ("entity_behavior", "primary_table"),
    ("entity_behavior", "primary_label"),
    ("entity_behavior", "primary_keywords"),
    ("entity_behavior", "primary_menu_filters"),
    ("entity_behavior", "primary_menu_options"),
    ("entity_behavior", "default_entity_prompt"),
    ("entity_behavior", "filter_context_prompt"),
    ("assistant_prompt", "suggested_queries"),
    ("domain_knowledge", "example_queries"),
)


class DomainRegistry:
    """
    Central registry for domain-specific configuration.
    Loads configuration from app/domains/{DOMAIN}/ folder.
    """

    _instance: Optional["DomainRegistry"] = None
    _instances: Dict[str, "DomainRegistry"] = {}
    _domain_name: str = ""
    _config: Dict[str, Any] = {}
    _manifest: Dict[str, Any] = {}
    _spec: Optional[DomainSpec] = None
    _enums_module: Any = None
    _fields_module: Any = None
    _rules_module: Any = None
    _fallback_domain_name: str = "starter"
    _domains_root_override: Optional[Path] = None
    _active_domain_name: ContextVar[Optional[str]] = ContextVar("tag_domain_name", default=None)
    _requested_domain_name: str = ""
    _base_domain_name: str = ""
    _load_diagnostics: Dict[str, Any] = {}

    def __init__(self, domain_name: Optional[str] = None):
        """Initialize domain registry with specified domain."""
        self._domain_name = domain_name or self._resolve_domain_name()
        self._requested_domain_name = self._domain_name
        self._base_domain_name = ""
        self._load_diagnostics = {}
        self._load_domain()

    @classmethod
    def get_current_domain(cls) -> "DomainRegistry":
        """Get singleton instance of current domain."""
        if cls._instance is None and cls._instances:
            cls._instances = {}

        domain_name = cls._resolve_domain_name()
        cached = cls._instances.get(domain_name)
        if cached is None:
            cached = cls(domain_name)
            cls._instances[domain_name] = cached
        cls._instance = cached
        return cls._instance

    @classmethod
    @contextmanager
    def use_domain(cls, domain_name: str | None):
        normalized = str(domain_name or "").strip() or None
        token = cls._active_domain_name.set(normalized)
        try:
            yield cls.get_current_domain()
        finally:
            cls._active_domain_name.reset(token)

    @classmethod
    def _resolve_domain_name(cls) -> str:
        # 1. Check ContextVar (active domain for current request)
        from_context = cls._active_domain_name.get()
        if from_context:
            return from_context

        # 2. Check Environment Variable
        explicit = str(os.getenv("DOMAIN", "") or "").strip()
        if explicit:
            return explicit

        # 3. Check App Settings
        try:
            configured = str(get_settings().DOMAIN or "").strip()
            if configured:
                return configured
        except Exception:
            pass
        return "vts"

    def _load_domain(self) -> None:
        """Load all domain configuration files."""
        domains_root = self._domains_root()
        requested_domain = str(self._domain_name or "").strip()
        self._requested_domain_name = requested_domain
        requested_path = domains_root / requested_domain

        fallback_domain = str(self._fallback_domain_name or "").strip() or "starter"
        fallback_path = domains_root / fallback_domain
        if not fallback_path.exists():
            try:
                fallback_domain = str(get_settings().DOMAIN or "").strip() or "vts"
            except Exception:
                fallback_domain = "vts"
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
        self._base_domain_name = fallback_domain if fallback_path.exists() else ""

        base_config, base_manifest = self._load_domain_package(fallback_path if fallback_path.exists() else None)
        active_config, active_manifest = self._load_domain_package(active_path)

        merged_config = self._merge_domain_config(base_config, active_config)
        merged_manifest = self._merge_manifest(base_manifest, active_manifest)
        self._spec = self.build_domain_spec(
            merged_config,
            merged_manifest,
            domain_name=self._domain_name,
        )
        self._config = self._spec.config_dict()
        self._manifest = self._spec.manifest_dict()
        self._load_diagnostics = self._build_effective_config_diagnostics(
            requested_domain=requested_domain,
            active_domain=active_domain,
            fallback_domain=(fallback_domain if fallback_path.exists() else ""),
            package_path=active_path,
            effective_config=self._config,
        )
        conflicts = self._load_diagnostics.get("conflicts") if isinstance(self._load_diagnostics, dict) else []
        if isinstance(conflicts, list) and conflicts:
            logger.warning(
                "Domain '%s' loaded with %d layer conflict(s): %s",
                active_domain,
                len(conflicts),
                ", ".join(
                    str(item.get("path", "")).strip()
                    for item in conflicts[:6]
                    if isinstance(item, dict) and str(item.get("path", "")).strip()
                ),
            )

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
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    @staticmethod
    def _nested_get(payload: Dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _stable_value_key(value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except Exception:
            return repr(value)

    @classmethod
    def _load_domain_layer_with_details(
        cls,
        layer_path: Optional[Path],
        layer_name: str,
    ) -> Dict[str, Any]:
        if not layer_path or not layer_path.exists():
            return {
                "name": layer_name,
                "path": str(layer_path) if layer_path else "",
                "files": [],
                "config_sections": [],
                "manifest_sections": [],
                "config": {},
                "manifest": {},
            }

        config: Dict[str, Any] = {}
        manifest: Dict[str, Any] = {}
        files: List[str] = []

        for json_path in sorted(layer_path.glob("*.json")):
            payload = cls._load_json_dict(json_path)
            if not payload:
                continue
            files.append(str(json_path.relative_to(layer_path.parent)))
            if json_path.name == "domain.json":
                config = cls._deep_merge_dicts(config, payload)
            elif json_path.name == "schema_manifest.json":
                manifest = cls._merge_manifest(manifest, payload)
            else:
                config = cls._deep_merge_dicts(config, cls._normalize_section_payload(json_path.stem, payload))

        manifest_dir = layer_path / "manifest"
        if manifest_dir.exists():
            for json_path in sorted(manifest_dir.glob("*.json")):
                payload = cls._load_json_dict(json_path)
                if not payload:
                    continue
                files.append(str(json_path.relative_to(layer_path.parent)))
                manifest = cls._merge_manifest(manifest, {json_path.stem: payload})

        return {
            "name": layer_name,
            "path": str(layer_path),
            "files": files,
            "config_sections": sorted(config.keys()),
            "manifest_sections": sorted(manifest.keys()),
            "config": config,
            "manifest": manifest,
        }

    @classmethod
    def _load_domain_package_with_details(cls, package_path: Optional[Path]) -> List[Dict[str, Any]]:
        if not package_path or not package_path.exists():
            return []

        legacy_config = cls._load_json_dict(package_path / "domain.json")
        legacy_manifest = cls._load_json_dict(package_path / "schema_manifest.json")
        legacy_files: List[str] = []
        if legacy_config:
            legacy_files.append("domain.json")
        if legacy_manifest:
            legacy_files.append("schema_manifest.json")
        for json_path in sorted(package_path.glob("*.json")):
            if json_path.name in {"domain.json", "schema_manifest.json", "review.json"}:
                continue
            payload = cls._load_json_dict(json_path)
            if not payload:
                continue
            legacy_files.append(str(json_path.relative_to(package_path)))
            legacy_config = cls._deep_merge_dicts(
                legacy_config,
                cls._normalize_section_payload(json_path.stem, payload),
            )

        layers = [
            {
                "name": "legacy",
                "path": str(package_path),
                "files": legacy_files,
                "config_sections": sorted(legacy_config.keys()),
                "manifest_sections": sorted(legacy_manifest.keys()),
                "config": legacy_config,
                "manifest": legacy_manifest,
            },
            cls._load_domain_layer_with_details(package_path / "generated", "generated"),
            cls._load_domain_layer_with_details(package_path / "manual", "manual"),
        ]
        return layers

    @classmethod
    def _detect_config_conflicts(
        cls,
        layers: List[Dict[str, Any]],
        effective_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        conflicts: List[Dict[str, Any]] = []
        for path in _CRITICAL_CONFIG_CONFLICT_PATHS:
            layer_values: Dict[str, Any] = {}
            distinct_values: Dict[str, Any] = {}
            for layer in layers:
                value = cls._nested_get(dict(layer.get("config") or {}), path)
                if not cls._has_meaningful_value(value):
                    continue
                layer_name = str(layer.get("name", "")).strip()
                if not layer_name:
                    continue
                layer_values[layer_name] = copy.deepcopy(value)
                distinct_values[cls._stable_value_key(value)] = value
            if len(distinct_values) <= 1:
                continue

            effective_source = ""
            for layer in reversed(layers):
                candidate = cls._nested_get(dict(layer.get("config") or {}), path)
                if cls._has_meaningful_value(candidate):
                    effective_source = str(layer.get("name", "")).strip()
                    break

            conflicts.append(
                {
                    "path": ".".join(path),
                    "layers": layer_values,
                    "effective_source": effective_source,
                    "effective_value": copy.deepcopy(cls._nested_get(effective_config, path)),
                }
            )
        return conflicts

    @classmethod
    def _build_effective_config_diagnostics(
        cls,
        *,
        requested_domain: str,
        active_domain: str,
        fallback_domain: str,
        package_path: Optional[Path],
        effective_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        layers = cls._load_domain_package_with_details(package_path)
        conflicts = cls._detect_config_conflicts(layers, effective_config)
        return {
            "requested_domain": requested_domain,
            "active_domain": active_domain,
            "fallback_domain": fallback_domain,
            "used_fallback_domain": bool(requested_domain and requested_domain != active_domain),
            "config_layers": [
                {
                    "name": str(layer.get("name", "")).strip(),
                    "path": str(layer.get("path", "")).strip(),
                    "files": list(layer.get("files") or []),
                    "config_sections": list(layer.get("config_sections") or []),
                    "manifest_sections": list(layer.get("manifest_sections") or []),
                }
                for layer in layers
            ],
            "conflicts": conflicts,
        }

    @classmethod
    def _domains_root(cls) -> Path:
        if cls._domains_root_override is not None:
            return Path(cls._domains_root_override)
        return Path(__file__).parent

    @classmethod
    def _load_domain_layer(cls, layer_path: Optional[Path]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if not layer_path or not layer_path.exists():
            return {}, {}

        config: Dict[str, Any] = {}
        manifest: Dict[str, Any] = {}

        for json_path in sorted(layer_path.glob("*.json")):
            payload = cls._load_json_dict(json_path)
            if not payload:
                continue
            if json_path.name == "domain.json":
                config = cls._deep_merge_dicts(config, payload)
            elif json_path.name == "schema_manifest.json":
                manifest = cls._merge_manifest(manifest, payload)
            else:
                config = cls._deep_merge_dicts(config, cls._normalize_section_payload(json_path.stem, payload))

        manifest_dir = layer_path / "manifest"
        if manifest_dir.exists():
            for json_path in sorted(manifest_dir.glob("*.json")):
                payload = cls._load_json_dict(json_path)
                if not payload:
                    continue
                manifest = cls._merge_manifest(manifest, {json_path.stem: payload})

        return config, manifest

    @classmethod
    def _load_domain_package(cls, package_path: Optional[Path]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if not package_path or not package_path.exists():
            return {}, {}

        legacy_config = cls._load_json_dict(package_path / "domain.json")
        legacy_manifest = cls._load_json_dict(package_path / "schema_manifest.json")
        for json_path in sorted(package_path.glob("*.json")):
            if json_path.name in {"domain.json", "schema_manifest.json", "review.json"}:
                continue
            payload = cls._load_json_dict(json_path)
            if not payload:
                continue
            legacy_config = cls._deep_merge_dicts(
                legacy_config,
                cls._normalize_section_payload(json_path.stem, payload),
            )
        generated_config, generated_manifest = cls._load_domain_layer(package_path / "generated")
        manual_config, manual_manifest = cls._load_domain_layer(package_path / "manual")

        config = cls._deep_merge_dicts(legacy_config, generated_config)
        config = cls._deep_merge_dicts(config, manual_config)

        manifest = cls._merge_manifest(legacy_manifest, generated_manifest)
        manifest = cls._merge_manifest(manifest, manual_manifest)

        return config, manifest

    @staticmethod
    def _normalize_section_payload(section_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        key = str(section_name or "").strip()
        if not key or not isinstance(payload, dict):
            return {}
        if key in payload and len(payload) == 1:
            value = payload.get(key)
            return {key: value if isinstance(value, dict) else value}
        return {key: payload}

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
    def _merge_domain_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge fallback-domain config into an active domain config.
        Top-level business sections should not inherit partial fallback content when the
        active domain defines them explicitly.
        """
        replace_sections = {"reports"}
        merged = DomainRegistry._deep_merge_dicts(base, override)
        for key in replace_sections:
            if key not in (override or {}):
                continue
            value = override.get(key)
            if isinstance(value, dict):
                merged[key] = dict(value)
            elif isinstance(value, list):
                merged[key] = list(value)
            else:
                merged[key] = value
        return merged

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

    @classmethod
    def build_domain_spec(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
        domain_name: str = "",
    ) -> DomainSpec:
        """Validate merged domain config/manifest and return typed domain spec."""
        label = str(domain_name or "unknown").strip() or "unknown"
        try:
            validated_config = DomainConfigModel.model_validate(dict(config or {}))
            validated_manifest = DomainManifestModel.model_validate(dict(manifest or {}))
            config_data = validated_config.model_dump(exclude_none=True)
            manifest_data = validated_manifest.model_dump(exclude_none=True)
            return DomainSpec(
                config=validated_config,
                manifest=validated_manifest,
                domain=cls._build_canonical_domain_section(config_data, label),
                schema_spec=cls._build_canonical_schema_section(validated_manifest, manifest_data, config_data),
                semantics=cls._build_canonical_semantics_section(config_data, manifest_data),
                capabilities=cls._build_canonical_capabilities_section(config_data, manifest_data),
                policies=cls._build_canonical_policies_section(config_data),
                language=cls._build_canonical_language_section(config_data, manifest_data),
                ux=cls._build_canonical_ux_section(config_data, manifest_data),
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid domain configuration for '{label}': {exc}") from exc

    @staticmethod
    def _build_canonical_domain_section(config: Dict[str, Any], domain_name: str) -> Dict[str, Any]:
        domain_id = str(config.get("name") or domain_name or "unknown").strip() or "unknown"
        display_name = str(config.get("bot_name") or config.get("name") or domain_id).strip() or domain_id
        version = str(config.get("version") or "1.0.0").strip() or "1.0.0"
        supported_locales = config.get("supported_locales")
        normalized_locales: List[str] = []
        if isinstance(supported_locales, list):
            for locale in supported_locales:
                text = str(locale or "").strip()
                if text and text not in normalized_locales:
                    normalized_locales.append(text)
        default_locale = str(config.get("default_locale") or "en").strip() or "en"
        if default_locale not in normalized_locales:
            normalized_locales.insert(0, default_locale)
        return {
            "id": domain_id,
            "name": display_name,
            "version": version,
            "description": str(config.get("description") or "").strip(),
            "default_locale": default_locale,
            "supported_locales": normalized_locales or ["en"],
        }

    @classmethod
    def _build_canonical_schema_section(
        cls,
        validated_manifest: DomainManifestModel,
        manifest: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        tenant_scopes: Dict[str, str] = {}
        for table_name, table_payload in (manifest.get("tables") or {}).items():
            if not isinstance(table_payload, dict):
                continue
            tenant_scope = table_payload.get("tenant_scope")
            column = ""
            if isinstance(tenant_scope, dict):
                column = str(tenant_scope.get("column") or "").strip()
            elif isinstance(tenant_scope, str):
                column = str(tenant_scope).strip()
            if not column:
                query_policy = config.get("query_policy")
                if isinstance(query_policy, dict) and table_name == str(query_policy.get("task_table_name") or "").strip():
                    column = str(query_policy.get("tenant_column") or "").strip()
            if column:
                tenant_scopes[str(table_name)] = column

        return {
            "tables": validated_manifest.tables,
            "tenant_scopes": tenant_scopes,
        }

    @classmethod
    def _build_canonical_semantics_section(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        entity_behavior = config.get("entity_behavior") if isinstance(config.get("entity_behavior"), dict) else {}
        user_lookup = config.get("user_lookup") if isinstance(config.get("user_lookup"), dict) else {}
        location_lookup = config.get("location_lookup") if isinstance(config.get("location_lookup"), dict) else {}
        query_policy = config.get("query_policy") if isinstance(config.get("query_policy"), dict) else {}

        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        entity_tables: Dict[str, str] = {}
        aliases: Dict[str, List[str]] = {}
        display_fields: Dict[str, List[str]] = {}
        searchable_fields: Dict[str, List[str]] = {}
        relations: Dict[str, List[str]] = {}

        for table_name, table_payload in tables.items():
            if not isinstance(table_payload, dict):
                continue
            key = str(table_name or "").strip()
            if not key:
                continue
            entity_tables[key] = key
            alias_values = cls._normalize_str_list(table_payload.get("aliases"))
            if key == str(entity_behavior.get("primary_table") or "").strip():
                alias_values = cls._merge_unique_lists(
                    alias_values,
                    cls._normalize_str_list(entity_behavior.get("primary_keywords")),
                    [str(entity_behavior.get("primary_label") or "").strip()],
                )
            if alias_values:
                aliases[key] = alias_values
            default_select_columns = cls._normalize_str_list(table_payload.get("default_select_columns"))
            if default_select_columns:
                display_fields[key] = default_select_columns
            important_columns = table_payload.get("important_columns")
            if isinstance(important_columns, dict):
                searchable_fields[key] = list(important_columns.keys())
            joins = table_payload.get("joins")
            if isinstance(joins, dict):
                relations[key] = [str(join_table) for join_table in joins.keys() if str(join_table or "").strip()]

        field_roles = {
            "status": cls._merge_unique_lists(
                cls._normalize_str_list(entity_behavior.get("status_filter_key")),
                cls._normalize_str_list(query_policy.get("status_filter_keys")),
            ),
            "priority": cls._merge_unique_lists(
                cls._normalize_str_list(entity_behavior.get("priority_filter_key")),
                cls._normalize_str_list(query_policy.get("priority_filter_keys")),
            ),
            "date": cls._merge_unique_lists(
                cls._normalize_str_list(entity_behavior.get("date_filter_keys")),
                cls._normalize_str_list(query_policy.get("date_filter_keys")),
            ),
            "user": cls._merge_unique_lists(
                cls._normalize_str_list(user_lookup.get("canonical_filter_key")),
                cls._normalize_str_list(user_lookup.get("id_filter_key")),
                cls._normalize_str_list(user_lookup.get("filter_keys")),
                cls._normalize_str_list(query_policy.get("user_filter_keys")),
            ),
            "location": cls._merge_unique_lists(
                cls._normalize_str_list(location_lookup.get("canonical_filter_key")),
                cls._normalize_str_list(location_lookup.get("id_filter_keys")),
                cls._normalize_str_list(location_lookup.get("filter_keys")),
                cls._normalize_str_list(query_policy.get("facility_filter_keys")),
            ),
            "tenant": cls._normalize_str_list(query_policy.get("tenant_column")),
        }

        primary_table = str(entity_behavior.get("primary_table") or "").strip()
        primary_entity = str(entity_behavior.get("primary_label") or primary_table).strip()
        
        semantics_cfg = config.get("semantics") or {}
        join_hints = semantics_cfg.get("join_hints") or {}
        column_logic = semantics_cfg.get("column_logic") or {}

        return {
            "primary_entity": primary_entity,
            "primary_table": primary_table,
            "entity_tables": entity_tables,
            "aliases": aliases,
            "field_roles": {key: value for key, value in field_roles.items() if value},
            "display_fields": display_fields,
            "searchable_fields": searchable_fields,
            "enum_columns": [],
            "relations": relations,
            "join_hints": dict(join_hints),
            "column_logic": dict(column_logic),
        }

    @classmethod
    def _build_canonical_capabilities_section(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        flows_enabled = cls._normalize_str_list(config.get("flows_enabled"))
        flow_bindings = config.get("flow_bindings") if isinstance(config.get("flow_bindings"), list) else []
        domain_knowledge = config.get("domain_knowledge") if isinstance(config.get("domain_knowledge"), dict) else {}
        domain_workflows = domain_knowledge.get("workflows") if isinstance(domain_knowledge.get("workflows"), list) else []
        report_payload = config.get("reports")
        if isinstance(report_payload, dict) and "reports" in report_payload and isinstance(report_payload.get("reports"), dict):
            report_payload = report_payload.get("reports")
        reports = dict(report_payload) if isinstance(report_payload, dict) else {}

        actions: List[Dict[str, Any]] = []
        for table_name, table_payload in tables.items():
            if not isinstance(table_payload, dict):
                continue
            operations = table_payload.get("operations")
            if not isinstance(operations, dict):
                continue
            for operation_name, operation_payload in operations.items():
                action_name = str(operation_name or "").strip()
                if not action_name:
                    continue
                action = {
                    "name": action_name,
                    "entity": str(table_name),
                    "executor": "sql_mutation",
                    "enabled": bool(
                        operation_payload.get("enabled", True) if isinstance(operation_payload, dict) else True
                    ),
                }
                if isinstance(operation_payload, dict):
                    required_fields = cls._normalize_str_list(operation_payload.get("required_fields"))
                    if required_fields:
                        action["required_fields"] = required_fields
                actions.append(action)

        workflows: List[Dict[str, Any]] = []
        for workflow_id in flows_enabled:
            workflows.append({"workflow_id": workflow_id, "executor": "flow"})
        for binding in flow_bindings:
            if not isinstance(binding, dict):
                continue
            workflow_id = str(binding.get("flow_id") or "").strip()
            if not workflow_id:
                continue
            workflows.append(
                {
                    "workflow_id": workflow_id,
                    "table": str(binding.get("table") or "").strip(),
                    "operation": str(binding.get("operation") or "").strip().lower(),
                    "executor": "flow",
                }
            )
        for workflow in domain_workflows:
            if not isinstance(workflow, dict):
                continue
            workflow_id = str(workflow.get("workflow_id") or "").strip()
            if not workflow_id:
                continue
            workflows.append(
                {
                    "workflow_id": workflow_id,
                    "label": str(workflow.get("label") or "").strip(),
                    "table": str(workflow.get("table") or "").strip(),
                    "operation": str(workflow.get("operation") or "").strip().lower(),
                    "required_fields": cls._normalize_str_list(workflow.get("required_fields")),
                    "executor": "workflow_candidate",
                }
            )

        capability_routes = ["chat"]
        if tables:
            capability_routes.append("query")
        if reports:
            capability_routes.append("report")
        if workflows:
            capability_routes.append("workflow")

        return {
            "routes": cls._merge_unique_lists(capability_routes),
            "actions": actions,
            "workflows": cls._dedupe_dicts(workflows, key_fields=("workflow_id", "table", "operation", "executor")),
            "reports": reports,
        }

    @classmethod
    def _build_canonical_policies_section(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        query_policy = config.get("query_policy") if isinstance(config.get("query_policy"), dict) else {}
        return {
            "query_policy": query_policy,
            "mutation_allowed_roles": cls._normalize_str_list(config.get("mutation_allowed_roles")),
            "protected_resources": cls._normalize_str_list(config.get("protected_resources")),
            "approval_rules": dict(config.get("approval_rules") or {}) if isinstance(config.get("approval_rules"), dict) else {},
            "output_rules": dict(config.get("summary") or {}) if isinstance(config.get("summary"), dict) else {},
        }

    @classmethod
    def _build_canonical_language_section(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        labels: Dict[str, Any] = {"tables": {}, "fields": {}}
        synonyms: Dict[str, List[str]] = {}

        for table_name, table_payload in tables.items():
            if not isinstance(table_payload, dict):
                continue
            key = str(table_name or "").strip()
            if not key:
                continue
            labels["tables"][key] = str(table_payload.get("description") or key).strip()
            aliases = cls._normalize_str_list(table_payload.get("aliases"))
            if aliases:
                synonyms[key] = aliases
            important_columns = table_payload.get("important_columns")
            if isinstance(important_columns, dict):
                field_labels: Dict[str, str] = {}
                for column_name, column_payload in important_columns.items():
                    if not isinstance(column_payload, dict):
                        continue
                    field_labels[str(column_name)] = str(column_payload.get("description") or column_name).strip()
                if field_labels:
                    labels["fields"][key] = field_labels

        domain_knowledge = config.get("domain_knowledge") or {}
        business_terms = domain_knowledge.get("business_terms") or {}
        glossary_cfg = config.get("glossary") or {}

        glossary = {}
        # Merge business_terms if they are a dict
        if isinstance(business_terms, dict):
            glossary.update({str(k): str(v) for k, v in business_terms.items()})
        # Merge glossary_cfg if it's a dict
        if isinstance(glossary_cfg, dict):
            glossary.update({str(k): str(v) for k, v in glossary_cfg.items()})

        response_templates = {}
        response_messages = config.get("response_messages")
        if isinstance(response_messages, dict):
            response_templates.update({str(key): str(value) for key, value in response_messages.items() if str(key or "").strip()})
        assistant_prompt = config.get("assistant_prompt")
        if isinstance(assistant_prompt, dict) and str(assistant_prompt.get("template") or "").strip():
            response_templates["assistant_prompt"] = str(assistant_prompt.get("template")).strip()

        return {
            "labels": labels,
            "synonyms": synonyms,
            "response_templates": response_templates,
            "glossary": glossary,
        }

    @classmethod
    def _build_canonical_ux_section(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        entity_behavior = config.get("entity_behavior") if isinstance(config.get("entity_behavior"), dict) else {}
        tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
        clarification_prompts: Dict[str, str] = {}
        for prompt_key in ("default_entity_prompt", "filter_context_prompt"):
            value = str(entity_behavior.get(prompt_key) or "").strip()
            if value:
                clarification_prompts[prompt_key] = value
        for table_name, table_payload in tables.items():
            if not isinstance(table_payload, dict):
                continue
            operations = table_payload.get("operations")
            if not isinstance(operations, dict):
                continue
            for operation_name, operation_payload in operations.items():
                if not isinstance(operation_payload, dict):
                    continue
                message = str(operation_payload.get("clarification_message") or "").strip()
                if message:
                    clarification_prompts[f"{table_name}.{operation_name}"] = message

        response_messages = config.get("response_messages")
        empty_state_messages: Dict[str, str] = {}
        if isinstance(response_messages, dict):
            for key, value in response_messages.items():
                text = str(key or "").strip()
                if "no_records" not in text:
                    continue
                empty_state_messages[text] = str(value or "").strip()

        disambiguation_rules = {}
        intent_detection = config.get("intent_detection")
        if isinstance(intent_detection, dict):
            disambiguation_rules = dict(intent_detection)

        return {
            "clarification_prompts": clarification_prompts,
            "empty_state_messages": empty_state_messages,
            "disambiguation_rules": disambiguation_rules,
        }

    @staticmethod
    def _normalize_str_list(value: Any) -> List[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if not isinstance(value, list):
            return []
        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @classmethod
    def _merge_unique_lists(cls, *values: Any) -> List[str]:
        merged: List[str] = []
        for value in values:
            for item in cls._normalize_str_list(value):
                if item not in merged:
                    merged.append(item)
        return merged

    @staticmethod
    def _dedupe_dicts(rows: List[Dict[str, Any]], key_fields: tuple[str, ...]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            key = tuple(str(row.get(field) or "").strip() for field in key_fields)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    @classmethod
    def validate_domain_artifacts(
        cls,
        config: Dict[str, Any],
        manifest: Dict[str, Any],
        domain_name: str = "",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        spec = cls.build_domain_spec(config, manifest, domain_name=domain_name)
        return spec.config_dict(), spec.manifest_dict()

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
        return self._domains_root() / self._domain_name

    @property
    def manifest(self) -> Dict[str, Any]:
        """Get schema manifest."""
        return self._manifest

    @property
    def config(self) -> Dict[str, Any]:
        """Get domain configuration."""
        if self._spec is None:
            return self._config
        return self._spec.config_dict()

    @property
    def spec(self) -> DomainSpec:
        """Get typed domain spec."""
        if self._spec is None:
            self._spec = self.build_domain_spec(self._config, self._manifest, domain_name=self._domain_name)
        return self._spec

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
        if callable(func):
            try:
                return func(table, required_fields, collected_fields, self._config)
            except TypeError:
                return func(table, required_fields, collected_fields)
            except Exception:
                return required_fields

        return required_fields

    def is_flow_candidate(self, message: str, table: str) -> bool:
        """Determine if message should trigger a flow."""
        if not self._rules_module:
            return False

        func = getattr(self._rules_module, "is_flow_candidate", None)
        if callable(func):
            try:
                return bool(func(message, table, self._config))
            except TypeError:
                return bool(func(message, table))
            except Exception:
                return False

        return False

    def normalize_flow_fields(self, table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Optional domain hook to normalize LLM/KV flow fields per table."""
        if not self._rules_module:
            return dict(fields or {})
        func = getattr(self._rules_module, "normalize_flow_fields", None)
        if not callable(func):
            return dict(fields or {})
        try:
            payload = func(str(table or "").strip(), dict(fields or {}), self._config)
            return dict(payload) if isinstance(payload, dict) else dict(fields or {})
        except Exception:
            return dict(fields or {})

    def resolve_flow_slot_prefill(
        self,
        message: str,
        table: str,
        operation: str,
        initial_fields: Dict[str, Any],
        allow_message_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Optional domain hook to compute flow slot prefill payload.
        Expected shape:
          {
            "values": {...},          # scalar fields to set directly
            "search": {...},          # resolver search hints
            "llm_slots_present": bool # whether LLM already provided actionable slots
          }
        """
        empty_payload = {"values": {}, "search": {}, "llm_slots_present": False}
        if not self._rules_module:
            return empty_payload
        func = getattr(self._rules_module, "resolve_flow_slot_prefill", None)
        if not callable(func):
            return empty_payload
        try:
            payload = func(
                str(message or ""),
                str(table or "").strip(),
                str(operation or "").strip().lower(),
                dict(initial_fields or {}),
                bool(allow_message_fallback),
                self._config,
            )
        except Exception:
            return empty_payload
        if not isinstance(payload, dict):
            return empty_payload
        values = payload.get("values")
        search = payload.get("search")
        return {
            "values": dict(values) if isinstance(values, dict) else {},
            "search": dict(search) if isinstance(search, dict) else {},
            "llm_slots_present": bool(payload.get("llm_slots_present", False)),
        }

    def get_capabilities(self) -> Dict[str, Any]:
        """Get domain capabilities for help/discovery."""
        return self._config.get("capabilities", {})

    def get_domain_knowledge_config(self) -> Dict[str, Any]:
        return self.get_config_section("domain_knowledge")

    def get_config_layer_diagnostics(self) -> Dict[str, Any]:
        return copy.deepcopy(self._load_diagnostics or {})

    def get_effective_config_summary(self) -> Dict[str, Any]:
        entity_behavior = self.get_entity_behavior_config()
        sql_builder = self.get_config_section("sql_builder")
        ui_cfg = sql_builder.get("ui") if isinstance(sql_builder.get("ui"), dict) else {}
        assistant_prompt = self.get_assistant_prompt_config()
        select_workflow = self.get_config_section("select_workflow")
        return {
            "requested_domain": self._requested_domain_name or self._domain_name,
            "active_domain": self._domain_name,
            "base_domain": self._base_domain_name,
            "used_fallback_domain": bool(
                self._requested_domain_name and self._requested_domain_name != self._domain_name
            ),
            "primary_table": str(entity_behavior.get("primary_table", "") or "").strip(),
            "primary_label": str(entity_behavior.get("primary_label", "") or "").strip(),
            "primary_keywords": [
                str(item).strip()
                for item in (entity_behavior.get("primary_keywords") or [])
                if str(item).strip()
            ],
            "primary_menu_options": [
                dict(item)
                for item in (entity_behavior.get("primary_menu_options") or [])
                if isinstance(item, dict)
            ],
            "default_entity_prompt": str(entity_behavior.get("default_entity_prompt", "") or "").strip(),
            "filter_context_prompt": str(entity_behavior.get("filter_context_prompt", "") or "").strip(),
            "task_menu_today_label": str(entity_behavior.get("task_menu_today_label", "") or "").strip(),
            "task_menu_today_value": str(entity_behavior.get("task_menu_today_value", "") or "").strip(),
            "filter_prompt_title_template": str(ui_cfg.get("filter_prompt_title_template", "") or "").strip(),
            "assistant_suggested_queries": [
                str(item).strip()
                for item in (assistant_prompt.get("suggested_queries") or [])
                if str(item).strip()
            ],
            "select_workflow": {
                "workflow_id": str(select_workflow.get("workflow_id", "") or "").strip(),
                "state": str(select_workflow.get("state", "") or "").strip(),
                "mode": str(select_workflow.get("mode", "") or "").strip(),
                "next_field": str(select_workflow.get("next_field", "") or "").strip(),
                "operation": str(select_workflow.get("operation", "") or "").strip(),
            },
            "layer_diagnostics": self.get_config_layer_diagnostics(),
        }

    def get_config_section(self, section: str) -> Dict[str, Any]:
        """Get a top-level domain config section as a dict."""
        return self.spec.get_config_section(section)

    def get_canonical_section(self, section: str) -> Dict[str, Any]:
        """Get a canonical domain spec section as a dict."""
        return self.spec.get_canonical_section(section)

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
                "config": dict(self._config or {}),
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
