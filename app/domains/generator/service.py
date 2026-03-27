from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import inspect

from app.domains.registry import DomainRegistry
from app.services.data.schema_service import SchemaService


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _pluralize(label: str) -> str:
    value = str(label or "").strip()
    lowered = value.lower()
    if not value:
        return value
    if lowered.endswith("y") and len(value) > 1 and lowered[-2] not in "aeiou":
        return value[:-1] + "ies"
    if lowered.endswith(("s", "x", "z", "ch", "sh")):
        return value + "es"
    return value + "s"


def _humanize(identifier: str) -> str:
    text = str(identifier or "").strip().replace("_", " ")
    return " ".join(part for part in text.split() if part)


def _titleize(identifier: str) -> str:
    return " ".join(part.capitalize() for part in _humanize(identifier).split())


def _slugify(identifier: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(identifier or "").strip().lower()).strip("_")


def _normalize_tokens(identifier: str) -> List[str]:
    return [token for token in _humanize(identifier).lower().split() if token]


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        normalized = cleaned.lower()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        out.append(cleaned)
    return out


@dataclass
class ReviewItem:
    key: str
    reason: str
    confidence: int
    inferred_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "key": self.key,
            "reason": self.reason,
            "confidence": int(self.confidence),
        }
        if self.inferred_value is not None:
            payload["inferred_value"] = self.inferred_value
        return payload


@dataclass
class ClarificationQuestion:
    key: str
    prompt: str
    help_text: str = ""
    default_value: Any = ""
    options: List[str] = field(default_factory=list)
    multi_value: bool = False
    allow_blank: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "key": self.key,
            "prompt": self.prompt,
            "help_text": self.help_text,
            "options": list(self.options),
            "multi_value": bool(self.multi_value),
            "allow_blank": bool(self.allow_blank),
        }
        if self.default_value not in ("", None, []):
            payload["default_value"] = copy.deepcopy(self.default_value)
        return payload


@dataclass
class DomainGenerationArtifacts:
    domain_name: str
    generated_config_sections: Dict[str, Dict[str, Any]]
    generated_manifest_sections: Dict[str, Any]
    root_json_files: Dict[str, Dict[str, Any]]
    root_text_files: Dict[str, str]
    review_report: Dict[str, Any]
    written_files: List[Path] = field(default_factory=list)

    def config_payload(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {}
        base = self.generated_config_sections.get("domain") or {}
        if isinstance(base, dict):
            config = _deep_merge(config, base)
        for key, value in self.generated_config_sections.items():
            if key == "domain" or not isinstance(value, dict):
                continue
            config = _deep_merge(config, {key: value})
        return config

    def manifest_payload(self) -> Dict[str, Any]:
        manifest: Dict[str, Any] = {}
        tables = self.generated_manifest_sections.get("tables")
        if isinstance(tables, dict):
            manifest["tables"] = copy.deepcopy(tables)
        query_templates = self.generated_manifest_sections.get("query_templates")
        if isinstance(query_templates, dict):
            manifest["query_templates"] = copy.deepcopy(query_templates)
        table_resolution_rules = self.generated_manifest_sections.get("table_resolution_rules")
        if isinstance(table_resolution_rules, list):
            manifest["table_resolution_rules"] = copy.deepcopy(table_resolution_rules)
        return manifest


class DomainGenerationService:
    _USER_TABLE_TERMS = {
        "user",
        "users",
        "person",
        "people",
        "employee",
        "employees",
        "staff",
        "member",
        "members",
        "technician",
        "technicians",
        "operator",
        "operators",
    }
    _LOCATION_TABLE_TERMS = {
        "location",
        "locations",
        "facility",
        "facilities",
        "site",
        "sites",
        "building",
        "buildings",
        "warehouse",
        "warehouses",
        "branch",
        "branches",
        "campus",
        "campuses",
    }
    _PRIMARY_TABLE_HINTS = {
        "task",
        "tasks",
        "work",
        "work item",
        "work items",
        "ticket",
        "tickets",
        "issue",
        "issues",
        "order",
        "orders",
        "request",
        "requests",
        "transaction",
        "transactions",
        "schedule",
        "schedules",
        "event",
        "events",
    }
    _TECHNICAL_TABLE_TERMS = {
        "audit",
        "history",
        "log",
        "logs",
        "migration",
        "migrations",
        "schema",
        "cache",
        "token",
        "session",
        "ai schema note",
        "report audit log",
    }
    _TENANT_COLUMN_CANDIDATES = (
        "company_id",
        "tenant_id",
        "organization_id",
        "organisation_id",
        "org_id",
        "business_id",
        "account_id",
        "client_id",
    )
    _STATUS_COLUMN_CANDIDATES = ("status", "state", "task_status", "work_status")
    _PRIORITY_COLUMN_CANDIDATES = ("priority", "severity", "rank")
    _DISPLAY_COLUMN_CANDIDATES = (
        "title",
        "name",
        "display_name",
        "full_name",
        "subject",
        "code",
        "email",
        "description",
    )
    _USER_ID_COLUMN_CANDIDATES = (
        "assignee_id",
        "assigned_user_id",
        "user_id",
        "owner_id",
        "employee_id",
        "person_id",
        "created_by",
        "updated_by",
    )
    _LOCATION_ID_COLUMN_CANDIDATES = (
        "location_id",
        "facility_id",
        "site_id",
        "building_id",
        "warehouse_id",
        "branch_id",
    )

    def __init__(
        self,
        schema_service: Optional[SchemaService] = None,
        domains_root: Optional[Path] = None,
        starter_domain_path: Optional[Path] = None,
    ) -> None:
        self.schema_service = schema_service
        self.domains_root = Path(domains_root) if domains_root is not None else Path(__file__).resolve().parents[1]
        self.starter_domain_path = (
            Path(starter_domain_path) if starter_domain_path is not None else self.domains_root / "starter"
        )

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def merge_metadata_hints(base: Optional[Dict[str, Any]], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return _deep_merge(dict(base or {}), dict(override or {}))

    def _starter_config(self) -> Dict[str, Any]:
        return self._load_json(self.starter_domain_path / "domain.json")

    def _schema(self) -> SchemaService:
        if self.schema_service is None:
            self.schema_service = SchemaService()
        return self.schema_service

    def introspect_schema(self, db_url: Optional[str] = None) -> Dict[str, Any]:
        schema_service = self._schema()
        target_url = str(db_url or getattr(schema_service, "default_db_url", "") or "").strip()
        engine = schema_service.get_engine_for_url(target_url)
        with engine.connect() as conn:
            inspector = inspect(conn)
            table_names = sorted(inspector.get_table_names())
            tables: List[Dict[str, Any]] = []
            for table_name in table_names:
                columns = inspector.get_columns(table_name)
                pk = inspector.get_pk_constraint(table_name) or {}
                foreign_keys = inspector.get_foreign_keys(table_name) or []
                indexes = inspector.get_indexes(table_name) or []
                tables.append(
                    {
                        "name": table_name,
                        "columns": [
                            {
                                "name": str(column.get("name") or "").strip(),
                                "type": str(column.get("type") or "").strip(),
                                "nullable": bool(column.get("nullable", True)),
                                "default": column.get("default"),
                            }
                            for column in columns
                            if str(column.get("name") or "").strip()
                        ],
                        "primary_key": [
                            str(column).strip()
                            for column in (pk.get("constrained_columns") or [])
                            if str(column).strip()
                        ],
                        "foreign_keys": [
                            {
                                "constrained_columns": [
                                    str(column).strip()
                                    for column in (foreign_key.get("constrained_columns") or [])
                                    if str(column).strip()
                                ],
                                "referred_table": str(foreign_key.get("referred_table") or "").strip(),
                                "referred_columns": [
                                    str(column).strip()
                                    for column in (foreign_key.get("referred_columns") or [])
                                    if str(column).strip()
                                ],
                            }
                            for foreign_key in foreign_keys
                            if str(foreign_key.get("referred_table") or "").strip()
                        ],
                        "indexes": [
                            {
                                "name": str(index.get("name") or "").strip(),
                                "column_names": [
                                    str(column).strip()
                                    for column in (index.get("column_names") or [])
                                    if str(column).strip()
                                ],
                                "unique": bool(index.get("unique", False)),
                            }
                            for index in indexes
                        ],
                    }
                )
        return {
            "database_target": SchemaService._safe_db_target(target_url),
            "table_count": len(tables),
            "tables": tables,
        }

    @staticmethod
    def _column_names(table: Dict[str, Any]) -> List[str]:
        return [str(column.get("name") or "").strip() for column in (table.get("columns") or []) if str(column.get("name") or "").strip()]

    @staticmethod
    def _table_names(tables: List[Dict[str, Any]]) -> List[str]:
        return [
            str(table.get("name") or "").strip()
            for table in tables
            if str(table.get("name") or "").strip()
        ]

    @classmethod
    def _table_by_name(cls, tables: List[Dict[str, Any]], table_name: str) -> Dict[str, Any]:
        target = str(table_name or "").strip().lower()
        if not target:
            return {}
        for table in tables:
            if str(table.get("name") or "").strip().lower() == target:
                return dict(table)
        return {}

    @classmethod
    def _find_column(cls, table: Dict[str, Any], candidates: Iterable[str]) -> str:
        names = {name.lower(): name for name in cls._column_names(table)}
        for candidate in candidates:
            value = names.get(str(candidate).strip().lower())
            if value:
                return value
        return ""

    @classmethod
    def _best_display_column(cls, table: Dict[str, Any]) -> str:
        match = cls._find_column(table, cls._DISPLAY_COLUMN_CANDIDATES)
        if match:
            return match
        for column in table.get("columns") or []:
            name = str(column.get("name") or "").strip()
            type_name = str(column.get("type") or "").lower()
            if not name:
                continue
            if name.lower() in {"id", "company_id", "tenant_id"}:
                continue
            if any(token in type_name for token in ("char", "text", "string")):
                return name
        primary_key = cls._primary_key(table)
        return primary_key or (cls._column_names(table)[0] if cls._column_names(table) else "id")

    @classmethod
    def _primary_key(cls, table: Dict[str, Any]) -> str:
        primary_key = [str(column).strip() for column in (table.get("primary_key") or []) if str(column).strip()]
        if primary_key:
            return primary_key[0]
        if cls._column_names(table):
            return cls._column_names(table)[0]
        return "id"

    @classmethod
    def _tenant_column(cls, table: Dict[str, Any]) -> str:
        match = cls._find_column(table, cls._TENANT_COLUMN_CANDIDATES)
        return match

    @classmethod
    def _status_column(cls, table: Dict[str, Any]) -> str:
        return cls._find_column(table, cls._STATUS_COLUMN_CANDIDATES)

    @classmethod
    def _priority_column(cls, table: Dict[str, Any]) -> str:
        return cls._find_column(table, cls._PRIORITY_COLUMN_CANDIDATES)

    @classmethod
    def _date_columns(cls, table: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for column in table.get("columns") or []:
            name = str(column.get("name") or "").strip()
            type_name = str(column.get("type") or "").lower()
            lowered = name.lower()
            if not name:
                continue
            if lowered in {"id", "company_id", "tenant_id"}:
                continue
            if "date" in lowered or lowered.endswith("_at") or lowered.endswith("_time"):
                candidates.append(name)
                continue
            if any(token in type_name for token in ("date", "time")):
                candidates.append(name)
        return _dedupe_keep_order(candidates)

    @classmethod
    def _table_aliases(cls, table_name: str, extra_aliases: Iterable[str] = ()) -> List[str]:
        humanized = _humanize(table_name)
        singular = humanized
        plural = _pluralize(humanized)
        aliases = [singular, plural]
        lowered = humanized.lower()
        if lowered in cls._USER_TABLE_TERMS or any(term == lowered for term in cls._USER_TABLE_TERMS):
            aliases.extend(["user", "users", "person", "people", "assignee", "assignees"])
        if lowered in cls._LOCATION_TABLE_TERMS or any(term == lowered for term in cls._LOCATION_TABLE_TERMS):
            aliases.extend(["location", "locations", "facility", "facilities", "site", "sites"])
        aliases.extend([str(item or "").strip() for item in extra_aliases if str(item or "").strip()])
        return _dedupe_keep_order(aliases)

    @staticmethod
    def _metadata_hints(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _hint_path_get(payload: Dict[str, Any], path: str) -> Any:
        current: Any = payload
        for part in [str(item or "").strip() for item in str(path or "").split(".") if str(item or "").strip()]:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @staticmethod
    def _hint_path_set(payload: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
        parts = [str(item or "").strip() for item in str(path or "").split(".") if str(item or "").strip()]
        if not parts:
            return payload
        target = payload
        for part in parts[:-1]:
            existing = target.get(part)
            if not isinstance(existing, dict):
                existing = {}
                target[part] = existing
            target = existing
        target[parts[-1]] = copy.deepcopy(value)
        return payload

    @classmethod
    def _metadata_table_roles(cls, metadata_hints: Dict[str, Any]) -> Dict[str, str]:
        payload = metadata_hints.get("table_roles")
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, str] = {}
        for key, value in payload.items():
            role = str(key or "").strip()
            table_name = str(value or "").strip()
            if role and table_name:
                out[role] = table_name
        return out

    @classmethod
    def _metadata_table_role(cls, metadata_hints: Dict[str, Any], role: str) -> str:
        roles = cls._metadata_table_roles(metadata_hints)
        return str(roles.get(str(role or "").strip(), "") or "").strip()

    @classmethod
    def _metadata_column_overrides(cls, metadata_hints: Dict[str, Any], table_name: str) -> Dict[str, Any]:
        payload = metadata_hints.get("column_overrides")
        if not isinstance(payload, dict):
            return {}
        target = str(table_name or "").strip()
        if not target:
            return {}
        for candidate in (target, target.lower()):
            value = payload.get(candidate)
            if isinstance(value, dict):
                return dict(value)
        return {}

    @classmethod
    def _validated_override_column(cls, table: Dict[str, Any], value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        available = {name.lower(): name for name in cls._column_names(table)}
        return available.get(candidate.lower(), "")

    @classmethod
    def _validated_override_columns(cls, table: Dict[str, Any], values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        available = {name.lower(): name for name in cls._column_names(table)}
        selected: List[str] = []
        for value in values:
            normalized = available.get(str(value or "").strip().lower())
            if normalized:
                selected.append(normalized)
        return _dedupe_keep_order(selected)

    @classmethod
    def _entity_hint(cls, metadata_hints: Dict[str, Any], table_name: str) -> Dict[str, Any]:
        entities = metadata_hints.get("entities")
        if not isinstance(entities, dict):
            return {}
        for candidate in (str(table_name or "").strip(), str(table_name or "").strip().lower()):
            hint = entities.get(candidate)
            if isinstance(hint, dict):
                return dict(hint)
        return {}

    @classmethod
    def _entity_alias_hints(cls, metadata_hints: Dict[str, Any], table_name: str) -> List[str]:
        hint = cls._entity_hint(metadata_hints, table_name)
        aliases = hint.get("aliases")
        values = [str(item or "").strip() for item in aliases if str(item or "").strip()] if isinstance(aliases, list) else []
        label = str(hint.get("label", "") or "").strip()
        if label:
            values.append(label)
        return _dedupe_keep_order(values)

    @classmethod
    def _entity_label_hint(cls, metadata_hints: Dict[str, Any], table_name: str, fallback: str) -> str:
        hint = cls._entity_hint(metadata_hints, table_name)
        label = str(hint.get("label", "") or "").strip()
        return label or fallback

    @classmethod
    def _entity_description_hint(cls, metadata_hints: Dict[str, Any], table_name: str, fallback: str) -> str:
        hint = cls._entity_hint(metadata_hints, table_name)
        description = str(hint.get("description", "") or "").strip()
        return description or fallback

    @classmethod
    def _entity_example_hints(cls, metadata_hints: Dict[str, Any], table_name: str) -> List[str]:
        hint = cls._entity_hint(metadata_hints, table_name)
        examples = hint.get("example_queries")
        if not isinstance(examples, list):
            return []
        return _dedupe_keep_order([str(item or "").strip() for item in examples if str(item or "").strip()])

    @staticmethod
    def _metadata_categorized_examples(metadata_hints: Dict[str, Any]) -> Dict[str, List[str]]:
        payload = metadata_hints.get("categorized_examples")
        if not isinstance(payload, dict):
            return {}
        normalized: Dict[str, List[str]] = {}
        for key, values in payload.items():
            label = str(key or "").strip()
            if not label or not isinstance(values, list):
                continue
            cleaned_values = _dedupe_keep_order([str(item or "").strip() for item in values if str(item or "").strip()])
            if cleaned_values:
                normalized[label] = cleaned_values
        return normalized

    @staticmethod
    def _metadata_business_terms(metadata_hints: Dict[str, Any]) -> Dict[str, str]:
        payload = metadata_hints.get("business_terms")
        if not isinstance(payload, dict):
            return {}
        normalized: Dict[str, str] = {}
        for key, value in payload.items():
            term = str(key or "").strip()
            meaning = str(value or "").strip()
            if term and meaning:
                normalized[term] = meaning
        return normalized

    @staticmethod
    def _metadata_example_queries(metadata_hints: Dict[str, Any]) -> List[str]:
        payload = metadata_hints.get("example_queries")
        if not isinstance(payload, list):
            return []
        return _dedupe_keep_order([str(item or "").strip() for item in payload if str(item or "").strip()])

    @staticmethod
    def _review_item_lookup(review_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        lookup: Dict[str, Dict[str, Any]] = {}
        for item in review_items:
            key = str(item.get("key") or "").strip()
            if key:
                lookup[key] = dict(item)
        return lookup

    def build_clarification_questions(
        self,
        schema_snapshot: Dict[str, Any],
        artifacts: DomainGenerationArtifacts,
        *,
        metadata_hints: Optional[Dict[str, Any]] = None,
        phase: str = "roles",
    ) -> List[ClarificationQuestion]:
        phase_name = str(phase or "").strip().lower() or "roles"
        if phase_name not in {"roles", "details"}:
            raise ValueError("phase must be 'roles' or 'details'")

        hints = self._metadata_hints(metadata_hints)
        tables = [dict(table) for table in (schema_snapshot.get("tables") or []) if isinstance(table, dict)]
        table_names = self._table_names(tables)
        review_items = artifacts.review_report.get("needs_review") or []
        review_lookup = self._review_item_lookup(
            [dict(item) for item in review_items if isinstance(item, dict)]
        )
        inference_summary = (
            dict(artifacts.review_report.get("inference_summary") or {})
            if isinstance(artifacts.review_report.get("inference_summary"), dict)
            else {}
        )
        questions: List[ClarificationQuestion] = []

        if phase_name == "roles":
            role_questions = [
                (
                    "primary_table",
                    "Which table should TAG treat as the main business entity users ask about most often?",
                    "Pick the table that best represents the core operational records for this domain.",
                ),
                (
                    "user_table",
                    "Which table represents people, assignees, or owners in this domain?",
                    "Pick the table developers would point to when explaining who owns or works on a record.",
                ),
                (
                    "location_table",
                    "Which table represents locations, facilities, sites, or operational scope?",
                    "Pick the table used to scope, filter, or group records by place or site.",
                ),
            ]
            for role_key, prompt, help_text in role_questions:
                review_key = {
                    "primary_table": "entity_behavior.primary_table",
                    "user_table": "user_lookup.table",
                    "location_table": "location_lookup.table",
                }[role_key]
                if self._hint_path_get(hints, f"table_roles.{role_key}"):
                    continue
                default_value = str(
                    ((inference_summary.get(role_key) or {}).get("value"))
                    or ""
                ).strip()
                if not default_value and role_key == "primary_table" and table_names:
                    default_value = table_names[0]
                if not default_value and role_key != "primary_table":
                    continue
                review_item = review_lookup.get(review_key, {})
                confidence = int(review_item.get("confidence") or 0)
                prompt_text = prompt
                if confidence and confidence < 80:
                    prompt_text = f"{prompt} Current inference is `{default_value}` with confidence {confidence}%."
                questions.append(
                    ClarificationQuestion(
                        key=f"table_roles.{role_key}",
                        prompt=prompt_text,
                        help_text=help_text,
                        default_value=default_value,
                        options=table_names[:12],
                    )
                )
            return questions

        primary_table_name = self._metadata_table_role(hints, "primary_table") or str(
            ((inference_summary.get("primary_table") or {}).get("value")) or ""
        ).strip()
        user_table_name = self._metadata_table_role(hints, "user_table") or str(
            ((inference_summary.get("user_table") or {}).get("value")) or ""
        ).strip()
        location_table_name = self._metadata_table_role(hints, "location_table") or str(
            ((inference_summary.get("location_table") or {}).get("value")) or ""
        ).strip()

        primary_table = self._table_by_name(tables, primary_table_name)
        user_table = self._table_by_name(tables, user_table_name)
        location_table = self._table_by_name(tables, location_table_name)

        semantic_tables = [
            (primary_table_name, primary_table, "primary entity"),
            (user_table_name, user_table, "user or owner entity"),
            (location_table_name, location_table, "location or scope entity"),
        ]
        for table_name, table, purpose in semantic_tables:
            if not table_name or not table:
                continue
            columns = ", ".join(self._column_names(table)[:10])
            if not self._hint_path_get(hints, f"entities.{table_name}.label"):
                questions.append(
                    ClarificationQuestion(
                        key=f"entities.{table_name}.label",
                        prompt=f"What user-facing plural label should TAG use for table `{table_name}` as the {purpose}?",
                        help_text=f"Use the business term developers/operators expect. Columns: {columns}",
                        default_value=self._entity_label_hint(
                            hints,
                            table_name,
                            _pluralize(_humanize(table_name)),
                        ),
                    )
                )
            if table_name == primary_table_name and not self._hint_path_get(hints, f"entities.{table_name}.aliases"):
                questions.append(
                    ClarificationQuestion(
                        key=f"entities.{table_name}.aliases",
                        prompt=f"What aliases or shorthand terms do people use for `{table_name}`?",
                        help_text="Enter comma-separated synonyms such as ticket, work order, asset, or case.",
                        default_value=self._table_aliases(
                            table_name,
                            extra_aliases=self._entity_alias_hints(hints, table_name),
                        ),
                        multi_value=True,
                    )
                )
            if not self._hint_path_get(hints, f"entities.{table_name}.description"):
                questions.append(
                    ClarificationQuestion(
                        key=f"entities.{table_name}.description",
                        prompt=f"In one short phrase, what does table `{table_name}` represent?",
                        help_text=f"Describe the business purpose of the table, not the raw schema. Columns: {columns}",
                        default_value=self._entity_description_hint(
                            hints,
                            table_name,
                            self._table_description(table),
                        ),
                    )
                )

        if primary_table:
            primary_columns = self._column_names(primary_table)
            column_overrides = self._metadata_column_overrides(hints, primary_table_name)
            detail_specs = [
                (
                    "column_overrides.{table}.tenant_column",
                    "manifest.tables.primary_table.tenant_scope",
                    "Which column on `{table}` defines tenant, company, or account scope?",
                    "Leave blank only if this table is intentionally not tenant-scoped.",
                    self._validated_override_column(primary_table, column_overrides.get("tenant_column"))
                    or self._tenant_column(primary_table),
                    False,
                    True,
                ),
                (
                    "column_overrides.{table}.status_column",
                    "entity_behavior.status_filter_key",
                    "Which column on `{table}` represents workflow or lifecycle status?",
                    "Choose the column TAG should use for status filters and summaries.",
                    self._validated_override_column(primary_table, column_overrides.get("status_column"))
                    or self._status_column(primary_table),
                    False,
                    False,
                ),
                (
                    "column_overrides.{table}.priority_column",
                    "entity_behavior.priority_filter_key",
                    "Which column on `{table}` represents priority or severity?",
                    "Choose the column TAG should use for priority-style filters.",
                    self._validated_override_column(primary_table, column_overrides.get("priority_column"))
                    or self._priority_column(primary_table),
                    False,
                    False,
                ),
                (
                    "column_overrides.{table}.date_columns",
                    "entity_behavior.date_filter_keys",
                    "Which date or time columns on `{table}` should TAG use for date filtering?",
                    "Enter one or more comma-separated columns in priority order.",
                    self._validated_override_columns(primary_table, column_overrides.get("date_columns"))
                    or self._date_columns(primary_table),
                    True,
                    False,
                ),
                (
                    "column_overrides.{table}.user_fk_columns",
                    "user_lookup.id_filter_key",
                    "Which column on `{table}` links records to the chosen user table?",
                    "Use the foreign key column that TAG should filter on for assignee or owner lookups.",
                    self._validated_override_columns(primary_table, column_overrides.get("user_fk_columns"))
                    or self._foreign_key_columns_for_table(primary_table, user_table_name),
                    True,
                    False,
                ),
                (
                    "column_overrides.{table}.location_fk_columns",
                    "location_lookup.id_filter_keys",
                    "Which column on `{table}` links records to the chosen location table?",
                    "Use the foreign key column that TAG should filter on for site or location lookups.",
                    self._validated_override_columns(primary_table, column_overrides.get("location_fk_columns"))
                    or self._foreign_key_columns_for_table(primary_table, location_table_name),
                    True,
                    False,
                ),
            ]
            for key_template, review_key, prompt_template, help_text, default_value, multi_value, allow_blank in detail_specs:
                review_item = review_lookup.get(review_key, {})
                hint_key = key_template.format(table=primary_table_name)
                if self._hint_path_get(hints, hint_key):
                    continue
                if review_item or not default_value:
                    questions.append(
                        ClarificationQuestion(
                            key=hint_key,
                            prompt=prompt_template.format(table=primary_table_name),
                            help_text=f"{help_text} Available columns: {', '.join(primary_columns[:12])}",
                            default_value=default_value,
                            options=primary_columns[:12],
                            multi_value=multi_value,
                            allow_blank=allow_blank,
                        )
                    )

        return questions

    def clarification_hints_from_answers(
        self,
        questions: List[ClarificationQuestion],
        answers: Dict[str, Any],
    ) -> Dict[str, Any]:
        hints: Dict[str, Any] = {}
        question_lookup = {question.key: question for question in questions}
        for key, raw_value in (answers or {}).items():
            question = question_lookup.get(str(key or "").strip())
            if question is None:
                continue
            if question.multi_value:
                if isinstance(raw_value, str):
                    values = [item.strip() for item in raw_value.split(",") if item.strip()]
                elif isinstance(raw_value, list):
                    values = [str(item or "").strip() for item in raw_value if str(item or "").strip()]
                else:
                    values = []
                if not values:
                    continue
                value: Any = values
            else:
                value = str(raw_value or "").strip()
                if not value:
                    continue
            self._hint_path_set(hints, question.key, value)
        return hints

    @classmethod
    def _detect_lookup_table(cls, tables: List[Dict[str, Any]], kind: str) -> tuple[Dict[str, Any], int]:
        best_table: Dict[str, Any] = {}
        best_score = -1
        keyword_set = cls._USER_TABLE_TERMS if kind == "user" else cls._LOCATION_TABLE_TERMS
        for table in tables:
            name = str(table.get("name") or "")
            tokens = set(_normalize_tokens(name))
            columns = {column.lower() for column in cls._column_names(table)}
            score = 0
            if tokens & keyword_set:
                score += 8
            if kind == "user":
                if {"first_name", "last_name"} <= columns:
                    score += 5
                if "email" in columns:
                    score += 2
                if "name" in columns:
                    score += 2
            else:
                if "name" in columns:
                    score += 4
                if {"name", "code"} <= columns:
                    score += 1
            if cls._tenant_column(table):
                score += 1
            if score > best_score:
                best_score = score
                best_table = table
        if best_score >= 12:
            confidence = 95
        elif best_score >= 8:
            confidence = 85
        elif best_score >= 4:
            confidence = 70
        elif best_score > 0:
            confidence = 55
        else:
            confidence = 0
        return best_table, confidence

    @classmethod
    def _foreign_key_columns_for_table(cls, table: Dict[str, Any], referred_table: str) -> List[str]:
        matches: List[str] = []
        for foreign_key in table.get("foreign_keys") or []:
            if str(foreign_key.get("referred_table") or "").strip() != str(referred_table or "").strip():
                continue
            matches.extend(
                [
                    str(column).strip()
                    for column in (foreign_key.get("constrained_columns") or [])
                    if str(column).strip()
                ]
            )
        return _dedupe_keep_order(matches)

    @classmethod
    def _detect_primary_table(
        cls,
        tables: List[Dict[str, Any]],
        user_table: Dict[str, Any],
        location_table: Dict[str, Any],
    ) -> tuple[Dict[str, Any], int]:
        user_name = str(user_table.get("name") or "").strip()
        location_name = str(location_table.get("name") or "").strip()
        best_table: Dict[str, Any] = {}
        best_score = -10_000
        for table in tables:
            name = str(table.get("name") or "").strip()
            tokens = set(_normalize_tokens(name))
            columns = {column.lower() for column in cls._column_names(table)}
            score = 0
            if tokens & cls._PRIMARY_TABLE_HINTS:
                score += 6
            if tokens & cls._USER_TABLE_TERMS:
                score -= 4
            if tokens & cls._LOCATION_TABLE_TERMS:
                score -= 3
            if tokens & cls._TECHNICAL_TABLE_TERMS:
                score -= 6
            if cls._status_column(table):
                score += 4
            if cls._date_columns(table):
                score += 3
            if cls._priority_column(table):
                score += 1
            if cls._best_display_column(table):
                score += 2
            if len(columns) >= 6:
                score += 1
            if user_name and cls._foreign_key_columns_for_table(table, user_name):
                score += 2
            if location_name and cls._foreign_key_columns_for_table(table, location_name):
                score += 2
            if score > best_score:
                best_score = score
                best_table = table
        if best_score >= 12:
            confidence = 95
        elif best_score >= 8:
            confidence = 85
        elif best_score >= 5:
            confidence = 70
        else:
            confidence = 55
        return best_table, confidence

    @classmethod
    def _default_select_columns(cls, table: Dict[str, Any]) -> List[str]:
        preferred = [
            cls._primary_key(table),
            cls._best_display_column(table),
            cls._status_column(table),
            cls._priority_column(table),
        ]
        preferred.extend(cls._date_columns(table)[:2])
        preferred.extend(cls._foreign_key_columns_for_table(table, ""))
        available = {name: name for name in cls._column_names(table)}
        ordered = [available.get(column, column) for column in preferred if str(column or "").strip()]
        if len(ordered) < 5:
            for column in cls._column_names(table):
                if column in ordered:
                    continue
                ordered.append(column)
                if len(ordered) >= 6:
                    break
        return _dedupe_keep_order(ordered[:6])

    @classmethod
    def _table_description(cls, table: Dict[str, Any]) -> str:
        name = str(table.get("name") or "").strip()
        humanized = _humanize(name)
        tokens = set(_normalize_tokens(name))
        if tokens & cls._USER_TABLE_TERMS:
            return f"{_titleize(humanized)} that can own or be associated with records"
        if tokens & cls._LOCATION_TABLE_TERMS:
            return f"{_titleize(humanized)} used for operational scoping"
        return f"{_titleize(humanized)} records"

    @classmethod
    def _status_phrase_map(cls) -> Dict[str, str]:
        return {
            "open": "Open",
            "pending": "Pending",
            "in progress": "In Progress",
            "in_progress": "In Progress",
            "completed": "Completed",
            "done": "Done",
            "closed": "Closed",
            "active": "Active",
            "inactive": "Inactive",
        }

    @staticmethod
    def _date_phrase_map() -> Dict[str, str]:
        return {
            "today": "today",
            "yesterday": "yesterday",
            "this week": "this_week",
            "last week": "last_week",
        }

    @staticmethod
    def _cleartm_reasoning_profile() -> Dict[str, Any]:
        return {
            "name": "ClearTM canonical AI reasoning",
            "behavior_summary": (
                "Direct answer first, one clarification if needed, and abstain instead of guessing when validated evidence is missing."
            ),
            "rules": [
                "frame only",
                "evidence first",
                "answer directly",
                "one clarification if blocked",
                "say when evidence is missing",
                "no invented data or causes",
                "no persona",
                "no internal reasoning trace",
                "no examples unless help was requested",
                "plain text",
            ],
            "response_modes": {
                "default": "direct answer, 1-4 short sentences",
                "help": "help <=5 lines, <=3 examples",
                "causal": "no cause inference",
                "count": "no data guessing",
                "lookup": "no data guessing",
            },
            "evidence_sources": ["sql_rowset", "domain_config", "runtime_state", "user_context"],
            "clarification_policy": "Ask one targeted clarification question when a single missing variable blocks the answer.",
            "abstention_policy": "If validated evidence is missing or conflicting, say so and stop.",
        }

    @classmethod
    def _review_item(
        cls,
        items: List[ReviewItem],
        key: str,
        reason: str,
        confidence: int,
        inferred_value: Any = None,
    ) -> None:
        if int(confidence) >= 80:
            return
        items.append(
            ReviewItem(
                key=key,
                reason=reason,
                confidence=int(confidence),
                inferred_value=inferred_value,
            )
        )

    @classmethod
    def _lookup_config_from_table(
        cls,
        table: Dict[str, Any],
        kind: str,
        fallback_tenant_column: str,
        inferred_filter_id_key: str,
        review_items: List[ReviewItem],
    ) -> Dict[str, Any]:
        table_name = str(table.get("name") or "").strip()
        primary_key = cls._primary_key(table)
        tenant_column = cls._tenant_column(table) or fallback_tenant_column or primary_key
        if kind == "user":
            first_name = cls._find_column(table, ("first_name", "given_name", "name", cls._best_display_column(table)))
            last_name = cls._find_column(table, ("last_name", "surname", "family_name", first_name))
            if not first_name or not last_name:
                cls._review_item(
                    review_items,
                    "user_lookup",
                    "No clear first/last name columns were found. Review generated user lookup fields.",
                    55,
                    {"table": table_name, "first_name_column": first_name or "", "last_name_column": last_name or ""},
                )
            return {
                "table": table_name,
                "id_column": primary_key,
                "first_name_column": first_name or cls._best_display_column(table),
                "last_name_column": last_name or cls._best_display_column(table),
                "tenant_column": tenant_column,
                "metadata_key": tenant_column,
                "filter_keys": ["assignee", "owner", "user"],
                "canonical_filter_key": "assignee",
                "id_filter_key": inferred_filter_id_key or "assignee_id",
                "search_limit": 12,
                "fallback_limit": 6,
                "fallback_name": "User",
            }
        return {
            "table": table_name,
            "name_column": cls._find_column(table, ("name", "display_name", cls._best_display_column(table)))
            or cls._best_display_column(table),
            "tenant_column": tenant_column,
            "metadata_key": tenant_column,
            "filter_keys": ["location_name", "location", "site", "facility"],
            "canonical_filter_key": "location_name",
            "id_filter_keys": [inferred_filter_id_key or "location_id"],
            "search_limit": 12,
            "fuzzy_scan_limit": 200,
            "fallback_limit": 6,
        }

    @classmethod
    def _table_manifest_entry(cls, table: Dict[str, Any], metadata_hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        table_name = str(table.get("name") or "").strip()
        primary_key = cls._primary_key(table)
        tenant_column = cls._tenant_column(table)
        hints = cls._metadata_hints(metadata_hints)
        entry: Dict[str, Any] = {
            "description": cls._entity_description_hint(hints, table_name, cls._table_description(table)),
            "primary_key": primary_key,
            "important_columns": {
                str(column.get("name") or "").strip(): {
                    "description": f"{_titleize(str(column.get('name') or '').strip())} ({str(column.get('type') or '').strip() or 'unknown'})"
                }
                for column in (table.get("columns") or [])
                if str(column.get("name") or "").strip()
            },
            "aliases": cls._table_aliases(table_name, extra_aliases=cls._entity_alias_hints(hints, table_name)),
        }
        if tenant_column:
            entry["tenant_scope"] = {
                "column": tenant_column,
                "template_var": tenant_column,
                "metadata_key": tenant_column,
            }
        joins: Dict[str, str] = {}
        for foreign_key in table.get("foreign_keys") or []:
            referred_table = str(foreign_key.get("referred_table") or "").strip()
            constrained_columns = [str(column).strip() for column in (foreign_key.get("constrained_columns") or []) if str(column).strip()]
            referred_columns = [str(column).strip() for column in (foreign_key.get("referred_columns") or []) if str(column).strip()]
            if not referred_table or not constrained_columns or not referred_columns:
                continue
            joins[referred_table] = f"{table_name}.{constrained_columns[0]} = {referred_table}.{referred_columns[0]}"
        if joins:
            entry["joins"] = joins
        default_select_columns = cls._default_select_columns(table)
        if default_select_columns:
            entry["default_select_columns"] = default_select_columns
        return entry

    @classmethod
    def _workflow_candidates(
        cls,
        primary_table: Dict[str, Any],
        primary_label: str,
        user_fk_columns: List[str],
        location_fk_columns: List[str],
        metadata_hints: Dict[str, Any],
        review_items: List[ReviewItem],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        workflow_hints = metadata_hints.get("workflows")
        if isinstance(workflow_hints, list):
            for item in workflow_hints:
                if not isinstance(item, dict):
                    continue
                table_name = str(item.get("table") or primary_table.get("name") or "").strip()
                operation = str(item.get("operation") or "insert").strip().lower() or "insert"
                workflow_id = str(item.get("workflow_id") or "").strip()
                label = str(item.get("label") or "").strip()
                if not workflow_id:
                    workflow_id = f"{operation}_{_slugify(label or table_name or primary_label)}"
                if not label:
                    label = _titleize(workflow_id)
                trigger_phrases = _dedupe_keep_order(
                    [str(value or "").strip() for value in (item.get("trigger_phrases") or []) if str(value or "").strip()]
                )
                required_fields = _dedupe_keep_order(
                    [str(value or "").strip() for value in (item.get("required_fields") or []) if str(value or "").strip()]
                )
                if not table_name or not workflow_id or not label:
                    continue
                normalized.append(
                    {
                        "workflow_id": workflow_id,
                        "label": label,
                        "table": table_name,
                        "operation": operation,
                        "trigger_phrases": trigger_phrases,
                        "required_fields": required_fields,
                        "reasoning": str(item.get("reasoning") or "Provided by project metadata hints.").strip(),
                        "confidence": max(0, min(100, int(item.get("confidence") or 95))),
                    }
                )
        if normalized:
            return normalized[:4]

        table_name = str(primary_table.get("name") or "").strip()
        singular_label = _humanize(table_name) or _humanize(primary_label)
        display_column = cls._best_display_column(primary_table)
        date_columns = cls._date_columns(primary_table)
        workflows = [
            {
                "workflow_id": f"create_{_slugify(singular_label or table_name)}",
                "label": f"Create {_titleize(singular_label or table_name)}",
                "table": table_name,
                "operation": "insert",
                "trigger_phrases": _dedupe_keep_order(
                    [
                        f"create {singular_label}",
                        f"add {singular_label}",
                    ]
                ),
                "required_fields": _dedupe_keep_order(
                    [display_column, user_fk_columns[0] if user_fk_columns else "", location_fk_columns[0] if location_fk_columns else "", date_columns[0] if date_columns else ""]
                )[:4],
                "reasoning": "Heuristic create workflow candidate inferred from the primary operational table.",
                "confidence": 55,
            }
        ]
        status_column = cls._status_column(primary_table)
        if status_column:
            workflows.append(
                {
                    "workflow_id": f"update_{_slugify(singular_label or table_name)}_status",
                    "label": f"Update {_titleize(singular_label or table_name)} Status",
                    "table": table_name,
                    "operation": "update",
                    "trigger_phrases": _dedupe_keep_order(
                        [
                            f"update {singular_label} status",
                            f"change {singular_label} status",
                        ]
                    ),
                    "required_fields": _dedupe_keep_order([cls._primary_key(primary_table), status_column]),
                    "reasoning": "Heuristic status-update workflow candidate inferred from the primary table status column.",
                    "confidence": 60,
                }
            )
        cls._review_item(
            review_items,
            "domain_knowledge.workflows",
            "Workflow candidates were inferred heuristically from the schema. Review them before relying on workflow guidance.",
            60,
            [item["workflow_id"] for item in workflows],
        )
        return workflows[:4]

    @classmethod
    def _query_template(cls, table: Dict[str, Any]) -> str:
        table_name = str(table.get("name") or "").strip()
        primary_key = cls._primary_key(table)
        default_columns = cls._default_select_columns(table)
        tenant_column = cls._tenant_column(table)
        template_var = tenant_column or "company_id"
        select_columns = ", ".join(f"{table_name}.{column}" for column in default_columns if str(column).strip())
        if not select_columns:
            select_columns = f"{table_name}.{primary_key}"
        where_parts = []
        if tenant_column:
            where_parts.append(f"{table_name}.{tenant_column} = {{{template_var}}}")
        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return (
            f"SELECT {select_columns} "
            f"FROM {table_name}"
            f"{where_clause} "
            f"ORDER BY {table_name}.{primary_key} DESC LIMIT 100;"
        )

    @classmethod
    def _status_summary_report(
        cls,
        primary_table: Dict[str, Any],
        primary_label: str,
    ) -> Dict[str, Any]:
        table_name = str(primary_table.get("name") or "").strip()
        status_column = cls._status_column(primary_table)
        if not table_name or not status_column:
            return {"reports": {}}
        tenant_column = cls._tenant_column(primary_table)
        where_clause = f" WHERE {table_name}.{tenant_column} = {{{tenant_column}}}" if tenant_column else ""
        report_id = f"{table_name}_status_summary"
        report_name = f"{_titleize(primary_label)} Status Summary"
        return {
            "reports": {
                report_id: {
                    "name": report_name,
                    "description": f"{_titleize(primary_label)} grouped by {status_column}",
                    "query": (
                        f"SELECT {table_name}.{status_column}, COUNT(*) AS count "
                        f"FROM {table_name}"
                        f"{where_clause} "
                        f"GROUP BY {table_name}.{status_column} ORDER BY count DESC;"
                    ),
                    "category": "summary",
                    "access_level": "user",
                }
            }
        }

    def build_artifacts(
        self,
        domain_name: str,
        schema_snapshot: Dict[str, Any],
        *,
        description: str = "",
        metadata_hints: Optional[Dict[str, Any]] = None,
    ) -> DomainGenerationArtifacts:
        normalized_domain = str(domain_name or "").strip().lower().replace(" ", "_")
        if not normalized_domain:
            raise ValueError("domain_name must not be empty")

        tables = [dict(table) for table in (schema_snapshot.get("tables") or []) if isinstance(table, dict)]
        if not tables:
            raise ValueError("Schema snapshot does not contain any tables")

        starter = self._starter_config()
        hints = self._metadata_hints(metadata_hints)
        review_items: List[ReviewItem] = []
        user_table_override = self._metadata_table_role(hints, "user_table")
        location_table_override = self._metadata_table_role(hints, "location_table")
        primary_table_override = self._metadata_table_role(hints, "primary_table")

        user_table = self._table_by_name(tables, user_table_override)
        user_confidence = 100 if user_table else 0
        if not user_table:
            user_table, user_confidence = self._detect_lookup_table(tables, kind="user")

        location_table = self._table_by_name(tables, location_table_override)
        location_confidence = 100 if location_table else 0
        if not location_table:
            location_table, location_confidence = self._detect_lookup_table(tables, kind="location")

        primary_table = self._table_by_name(tables, primary_table_override)
        primary_confidence = 100 if primary_table else 0
        if not primary_table:
            primary_table, primary_confidence = self._detect_primary_table(tables, user_table, location_table)

        primary_table_name = str(primary_table.get("name") or "").strip()
        if not primary_table_name:
            primary_table = tables[0]
            primary_table_name = str(primary_table.get("name") or "").strip()
            primary_confidence = 40
        self._review_item(
            review_items,
            "entity_behavior.primary_table",
            "Primary business table was inferred heuristically.",
            primary_confidence,
            primary_table_name,
        )

        user_table_name = str(user_table.get("name") or "").strip() or primary_table_name
        location_table_name = str(location_table.get("name") or "").strip() or primary_table_name
        self._review_item(
            review_items,
            "user_lookup.table",
            "User lookup table was inferred heuristically.",
            user_confidence,
            user_table_name,
        )
        self._review_item(
            review_items,
            "location_lookup.table",
            "Location lookup table was inferred heuristically.",
            location_confidence,
            location_table_name,
        )

        primary_label = self._entity_label_hint(hints, primary_table_name, _pluralize(_humanize(primary_table_name)))
        primary_aliases = self._table_aliases(primary_table_name, extra_aliases=self._entity_alias_hints(hints, primary_table_name))
        primary_keywords = primary_aliases[:]
        if not primary_keywords:
            primary_keywords = [primary_label]
        primary_column_overrides = self._metadata_column_overrides(hints, primary_table_name)
        date_columns = self._validated_override_columns(primary_table, primary_column_overrides.get("date_columns")) or self._date_columns(primary_table)
        status_column = self._validated_override_column(primary_table, primary_column_overrides.get("status_column")) or self._status_column(primary_table) or "status"
        priority_column = self._validated_override_column(primary_table, primary_column_overrides.get("priority_column")) or self._priority_column(primary_table) or "priority"
        if not (
            self._validated_override_column(primary_table, primary_column_overrides.get("status_column"))
            or self._status_column(primary_table)
        ):
            self._review_item(
                review_items,
                "entity_behavior.status_filter_key",
                "No clear status column was found. A placeholder status column was emitted.",
                45,
                status_column,
            )
        if not (
            self._validated_override_column(primary_table, primary_column_overrides.get("priority_column"))
            or self._priority_column(primary_table)
        ):
            self._review_item(
                review_items,
                "entity_behavior.priority_filter_key",
                "No clear priority column was found. A placeholder priority column was emitted.",
                45,
                priority_column,
            )
        if not date_columns:
            fallback_date = self._best_display_column(primary_table) or self._primary_key(primary_table)
            date_columns = [fallback_date]
            self._review_item(
                review_items,
                "entity_behavior.date_filter_keys",
                "No clear date or time column was found. Review generated date filter keys.",
                45,
                date_columns,
            )

        user_fk_columns = self._validated_override_columns(primary_table, primary_column_overrides.get("user_fk_columns")) or self._foreign_key_columns_for_table(primary_table, user_table_name)
        if not user_fk_columns:
            user_fk_columns = [self._find_column(primary_table, self._USER_ID_COLUMN_CANDIDATES) or "assignee_id"]
            self._review_item(
                review_items,
                "user_lookup.id_filter_key",
                "No explicit foreign key to the user table was found on the primary table.",
                45,
                user_fk_columns[0],
            )
        location_fk_columns = self._validated_override_columns(primary_table, primary_column_overrides.get("location_fk_columns")) or self._foreign_key_columns_for_table(primary_table, location_table_name)
        if not location_fk_columns:
            fallback_location_key = self._find_column(primary_table, self._LOCATION_ID_COLUMN_CANDIDATES) or "location_id"
            location_fk_columns = [fallback_location_key]
            self._review_item(
                review_items,
                "location_lookup.id_filter_keys",
                "No explicit foreign key to the location table was found on the primary table.",
                45,
                location_fk_columns,
            )

        primary_tenant_column = self._validated_override_column(primary_table, primary_column_overrides.get("tenant_column")) or self._tenant_column(primary_table) or "company_id"
        if not (
            self._validated_override_column(primary_table, primary_column_overrides.get("tenant_column"))
            or self._tenant_column(primary_table)
        ):
            self._review_item(
                review_items,
                "manifest.tables.primary_table.tenant_scope",
                "No tenant column was found on the primary table. Review multi-tenant scoping.",
                40,
                primary_tenant_column,
            )

        user_label = self._entity_label_hint(hints, user_table_name, _pluralize(_humanize(user_table_name)))
        location_label = self._entity_label_hint(hints, location_table_name, _pluralize(_humanize(location_table_name)))
        workflow_candidates = self._workflow_candidates(
            primary_table,
            primary_label,
            user_fk_columns,
            location_fk_columns,
            hints,
            review_items,
        )
        workflow_examples = [
            phrases[0]
            for phrases in (item.get("trigger_phrases") or [] for item in workflow_candidates)
            if phrases
        ]
        assistant_examples = [
            f"show {primary_label}",
            f"count {primary_label}",
        ]
        if user_table_name and user_table_name != primary_table_name:
            assistant_examples.append(f"list {user_label}")
        domain_entities = _dedupe_keep_order(
            [
                primary_label,
                user_label if user_table_name else "",
                location_label if location_table_name else "",
            ]
        )
        metadata_examples = self._metadata_example_queries(hints)
        assistant_examples = _dedupe_keep_order(
            metadata_examples
            + self._entity_example_hints(hints, primary_table_name)
            + self._entity_example_hints(hints, user_table_name)
            + self._entity_example_hints(hints, location_table_name)
            + assistant_examples
            + workflow_examples
        )
        domain_scope = (
            str(hints.get("scope") or "").strip()
            or f"{_humanize(normalized_domain)} operations including {', '.join(domain_entities[:3])}"
        )
        reasoning_profile = self._cleartm_reasoning_profile()
        categorized_examples = self._metadata_categorized_examples(hints)
        if not categorized_examples:
            categorized_examples = {}
            primary_examples = _dedupe_keep_order(
                self._entity_example_hints(hints, primary_table_name) + assistant_examples[:3]
            )
            if primary_examples:
                categorized_examples[_titleize(primary_label)] = primary_examples[:3]
            user_examples = self._entity_example_hints(hints, user_table_name)
            if user_examples:
                categorized_examples[_titleize(user_label)] = user_examples[:2]
            location_examples = self._entity_example_hints(hints, location_table_name)
            if location_examples:
                categorized_examples[_titleize(location_label)] = location_examples[:2]
            if workflow_examples:
                categorized_examples["Actions"] = workflow_examples[:3]

        business_terms = {
            primary_label: f"Primary operational records stored in `{primary_table_name}`.",
            user_label: f"People records stored in `{user_table_name}`.",
            location_label: f"Location records stored in `{location_table_name}`.",
        }
        for alias in primary_aliases[:6]:
            normalized_alias = str(alias or "").strip()
            if normalized_alias and normalized_alias.lower() != primary_label.lower():
                business_terms.setdefault(normalized_alias, f"Alias for {primary_label}.")
        business_terms.update(self._metadata_business_terms(hints))

        capabilities = {
            "description": f"I help you manage {domain_scope}",
            "examples": assistant_examples[:6],
            "categorized_examples": categorized_examples,
            "tables_description": {
                str(table.get("name") or "").strip(): self._entity_description_hint(
                    hints,
                    str(table.get("name") or "").strip(),
                    self._table_description(table),
                )
                for table in tables
                if str(table.get("name") or "").strip()
            },
        }

        domain_section = {
            "name": normalized_domain,
            "bot_name": f"{_titleize(normalized_domain)} Assistant",
            "description": description or (
                f"I'm {_titleize(normalized_domain)} Assistant, here to help you query and manage your {_humanize(normalized_domain)} data."
            ),
            "version": "0.1.0",
            "flows_enabled": [],
            "flow_bindings": [],
            "assistant_prompt": _deep_merge(
                starter.get("assistant_prompt") or {},
                {
                    "role_description": f"a practical assistant for {_humanize(normalized_domain)} operations",
                    "suggested_queries": assistant_examples[:2],
                    "compact_reasoning": {
                        "engine_label": reasoning_profile["name"],
                        "rules": list(reasoning_profile["rules"]),
                        "response_modes": dict(reasoning_profile["response_modes"]),
                    },
                },
            ),
            "intent_detection": _deep_merge(
                starter.get("intent_detection") or {},
                {
                    "assistant_context": f"{_humanize(normalized_domain)} reporting assistant",
                },
            ),
            "summary": _deep_merge(
                starter.get("summary") or {},
                {
                    "entity_label": primary_label,
                    "status_column": status_column,
                },
            ),
            "capabilities": capabilities,
        }
        domain_knowledge = {
            "scope": domain_scope,
            "primary_entities": domain_entities[:3],
            "business_terms": business_terms,
            "example_queries": assistant_examples[:6],
            "categorized_examples": categorized_examples,
            "workflows": workflow_candidates,
            "reasoning_profile": reasoning_profile,
        }

        entity_behavior = _deep_merge(
            starter.get("entity_behavior") or {},
            {
                "primary_table": primary_table_name,
                "primary_keywords": primary_keywords,
                "primary_filter_keys": _dedupe_keep_order(
                    date_columns
                    + [status_column, priority_column]
                    + user_fk_columns
                    + ["assignee", "owner", "user"]
                    + location_fk_columns
                    + ["location_name", "location", "site", "facility"]
                ),
                "primary_label": primary_label,
                "date_filter_keys": date_columns,
                "status_filter_key": status_column,
                "priority_filter_key": priority_column,
                "date_phrase_map": self._date_phrase_map(),
                "status_phrase_map": self._status_phrase_map(),
                "primary_menu_filters": _dedupe_keep_order(
                    [date_columns[0], status_column, user_fk_columns[0], priority_column]
                ),
                "primary_menu_options": [
                    {
                        "label": f"Today ({primary_label})",
                        "value": f"{date_columns[0]}=today",
                    },
                    {
                        "label": "Status",
                        "value": f"{status_column}=",
                    },
                    {
                        "label": "Owner / assignee",
                        "value": "assignee=",
                    },
                    {
                        "label": "Location",
                        "value": "location_name=",
                    },
                ],
                "date_range_terms": ["yesterday", "last week", "this week", "month", "range", "between"],
                "default_entity_prompt": f"Please mention an entity like `{primary_label}`.",
                "filter_context_prompt": f"I need context for that filter input. Start with an entity like `show {primary_label}` first.",
                "task_menu_today_label": f"Today ({primary_label})",
                "task_menu_today_value": f"{date_columns[0]}=today",
                "self_default_date_value": "today",
                "explicit_list_request_patterns": ["^\\s*(?:list|show|get|find|view|which)\\b"],
            },
        )

        user_lookup = self._lookup_config_from_table(
            user_table or primary_table,
            "user",
            primary_tenant_column,
            user_fk_columns[0],
            review_items,
        )
        location_lookup = self._lookup_config_from_table(
            location_table or primary_table,
            "location",
            primary_tenant_column,
            location_fk_columns[0],
            review_items,
        )
        select_workflow = copy.deepcopy(starter.get("select_workflow") or {})
        sql_builder = _deep_merge(
            starter.get("sql_builder") or {},
            {
                "table_alias_overrides": {
                    user_table_name: "user",
                    _pluralize(_humanize(user_table_name)).replace(" ", "_"): "user",
                    location_table_name: "location",
                },
            },
        )

        tables_manifest: Dict[str, Any] = {}
        query_templates: Dict[str, Any] = {}
        for table in tables:
            table_name = str(table.get("name") or "").strip()
            if not table_name:
                continue
            tables_manifest[table_name] = self._table_manifest_entry(table, metadata_hints=hints)
            query_templates[table_name] = {"list": self._query_template(table)}

        table_resolution_rules = [
            {
                "priority": 100,
                "target_table": primary_table_name,
                "any_terms": primary_aliases,
            }
        ]
        if user_table_name and user_table_name != primary_table_name:
            table_resolution_rules.append(
                {
                    "priority": 90,
                    "target_table": user_table_name,
                    "any_terms": self._table_aliases(user_table_name, extra_aliases=self._entity_alias_hints(hints, user_table_name)),
                }
            )
        if location_table_name and location_table_name not in {primary_table_name, user_table_name}:
            table_resolution_rules.append(
                {
                    "priority": 90,
                    "target_table": location_table_name,
                    "any_terms": self._table_aliases(location_table_name, extra_aliases=self._entity_alias_hints(hints, location_table_name)),
                }
            )

        generated_config_sections = {
            "domain": domain_section,
            "domain_knowledge": domain_knowledge,
            "entity_behavior": entity_behavior,
            "user_lookup": user_lookup,
            "location_lookup": location_lookup,
            "select_workflow": select_workflow,
            "sql_builder": sql_builder,
        }
        generated_manifest_sections = {
            "tables": tables_manifest,
            "query_templates": query_templates,
            "table_resolution_rules": table_resolution_rules,
        }

        config_payload = DomainGenerationArtifacts(
            domain_name=normalized_domain,
            generated_config_sections=generated_config_sections,
            generated_manifest_sections=generated_manifest_sections,
            root_json_files={},
            root_text_files={},
            review_report={},
        ).config_payload()
        manifest_payload = DomainGenerationArtifacts(
            domain_name=normalized_domain,
            generated_config_sections=generated_config_sections,
            generated_manifest_sections=generated_manifest_sections,
            root_json_files={},
            root_text_files={},
            review_report={},
        ).manifest_payload()
        DomainRegistry.validate_domain_artifacts(config_payload, manifest_payload, domain_name=normalized_domain)

        review_report = {
            "domain_name": normalized_domain,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "database_target": str(schema_snapshot.get("database_target") or ""),
            "table_count": int(schema_snapshot.get("table_count") or len(tables)),
            "inference_summary": {
                "primary_table": {
                    "value": primary_table_name,
                    "confidence": int(primary_confidence),
                },
                "user_table": {
                    "value": user_table_name,
                    "confidence": int(user_confidence),
                },
                "location_table": {
                    "value": location_table_name,
                    "confidence": int(location_confidence),
                },
            },
            "metadata_hints_applied": {
                "scope_override": bool(str(hints.get("scope") or "").strip()),
                "entity_hint_tables": sorted(
                    str(key or "").strip()
                    for key in ((hints.get("entities") or {}).keys() if isinstance(hints.get("entities"), dict) else [])
                    if str(key or "").strip()
                ),
                "business_term_count": len(self._metadata_business_terms(hints)),
                "workflow_count": len(workflow_candidates),
                "table_role_overrides": sorted(self._metadata_table_roles(hints).keys()),
                "column_override_tables": sorted(
                    str(key or "").strip()
                    for key in ((hints.get("column_overrides") or {}).keys() if isinstance(hints.get("column_overrides"), dict) else [])
                    if str(key or "").strip()
                ),
            },
            "needs_review": [item.to_dict() for item in review_items],
            "manual_override_suggestions": [
                "manual/domain_knowledge.json",
                "manual/assistant_prompt.json",
                "manual/entity_behavior.json",
                "manual/user_lookup.json",
                "manual/location_lookup.json",
                "manual/manifest/query_templates.json",
            ],
        }

        root_json_files = {
            "reports.json": self._status_summary_report(primary_table, primary_label),
            "review_report.json": review_report,
        }
        root_text_files = {
            "__init__.py": '"""Generated domain package."""\n',
            "enums.py": "ENUM_MAPPINGS = {}\nENUM_LABELS = {}\n",
            "fields.py": "FIELD_LABELS = {}\nFIELD_OPTIONS = {}\nLOOKUP_CONFIGS = {}\n",
            "rules.py": (
                '"""Manual domain hooks for generated domain packages."""\n'
            ),
            "manual/README.md": (
                "# Manual Overrides\n\n"
                "Place reviewed overrides in this folder. JSON files here override matching generated sections.\n\n"
                "Review `generated/domain_knowledge.json` first. It carries the ClearTM reasoning contract, scope, entities, examples, business terms, and workflow candidates that should travel with the domain when TAG is moved to another project.\n"
            ),
            "flows/README.md": "# Domain Flows\n\nAdd reviewed YAML flow definitions here when needed.\n",
        }
        return DomainGenerationArtifacts(
            domain_name=normalized_domain,
            generated_config_sections=generated_config_sections,
            generated_manifest_sections=generated_manifest_sections,
            root_json_files=root_json_files,
            root_text_files=root_text_files,
            review_report=review_report,
        )

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")

    def write_artifacts(
        self,
        artifacts: DomainGenerationArtifacts,
        *,
        output_root: Optional[Path] = None,
        force: bool = False,
    ) -> DomainGenerationArtifacts:
        root = Path(output_root) if output_root is not None else self.domains_root
        domain_dir = root / artifacts.domain_name
        if domain_dir.exists() and any(domain_dir.iterdir()) and not force:
            raise FileExistsError(
                f"Domain directory already exists: {domain_dir}. Use force=True to overwrite known generated files."
            )

        written: List[Path] = []
        generated_dir = domain_dir / "generated"
        manifest_dir = generated_dir / "manifest"

        for section_name, payload in artifacts.generated_config_sections.items():
            target = generated_dir / f"{section_name}.json"
            self._write_json(target, payload)
            written.append(target)

        for section_name, payload in artifacts.generated_manifest_sections.items():
            target = manifest_dir / f"{section_name}.json"
            self._write_json(target, payload)
            written.append(target)

        for relative_path, payload in artifacts.root_json_files.items():
            target = domain_dir / relative_path
            self._write_json(target, payload)
            written.append(target)

        for relative_path, content in artifacts.root_text_files.items():
            target = domain_dir / relative_path
            self._write_text(target, content)
            written.append(target)

        artifacts.written_files = written
        return artifacts

    def generate_domain(
        self,
        *,
        domain_name: str,
        db_url: Optional[str] = None,
        output_root: Optional[Path] = None,
        description: str = "",
        metadata_hints: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> DomainGenerationArtifacts:
        snapshot = self.introspect_schema(db_url=db_url)
        artifacts = self.build_artifacts(
            domain_name,
            snapshot,
            description=description,
            metadata_hints=metadata_hints,
        )
        return self.write_artifacts(artifacts, output_root=output_root, force=force)
