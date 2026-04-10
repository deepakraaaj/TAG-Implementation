from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from sqlalchemy import inspect

from app.schemas.onboarding import (
    SimpleOnboardingArtifact,
    SimpleOnboardingRelationship,
    SimpleOnboardingRequest,
    SimpleOnboardingResponse,
    SimpleOnboardingTable,
)
from app.services.data.schema_service import SchemaService


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        normalized = cleaned.lower()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        output.append(cleaned)
    return output


def _humanize(identifier: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", " ", str(identifier or "")).strip().lower()
    return " ".join(part for part in text.split() if part)


def _titleize(identifier: str) -> str:
    return " ".join(part.capitalize() for part in _humanize(identifier).split())


def _tokens(*values: str) -> set[str]:
    found: set[str] = set()
    for value in values:
        for token in re.split(r"[^a-zA-Z0-9]+", str(value or "").lower()):
            cleaned = token.strip()
            if cleaned:
                found.add(cleaned)
    return found


def _join_natural(items: List[str]) -> str:
    values = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


@dataclass
class _TableProfile:
    name: str
    columns: List[Dict[str, Any]]
    primary_key: List[str] = field(default_factory=list)
    foreign_keys: List[Dict[str, Any]] = field(default_factory=list)
    incoming_tables: List[str] = field(default_factory=list)

    @property
    def column_names(self) -> List[str]:
        return [
            str(column.get("name") or "").strip()
            for column in self.columns
            if str(column.get("name") or "").strip()
        ]

    @property
    def related_tables(self) -> List[str]:
        related = [str(fk.get("referred_table") or "").strip() for fk in self.foreign_keys]
        related.extend(self.incoming_tables)
        return sorted(_dedupe_keep_order(related))


class SimpleOnboardingService:
    _NOISE_TERMS = {
        "audit",
        "cache",
        "eventlog",
        "history",
        "jobrun",
        "log",
        "logs",
        "migration",
        "migrations",
        "schema",
        "session",
        "temp",
        "tmp",
        "token",
    }
    _BRIDGE_TERMS = {
        "association",
        "bridge",
        "junction",
        "link",
        "links",
        "mapping",
        "mappings",
        "relation",
        "relations",
        "xref",
    }
    _HOUSEKEEPING_COLUMNS = {
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by",
        "updated_by",
        "tenant_id",
        "company_id",
        "organization_id",
        "organisation_id",
        "org_id",
        "is_active",
        "is_deleted",
        "version",
    }
    _DATE_TERMS = {"date", "datetime", "time", "timestamp", "scheduled", "due", "created", "updated", "occurred"}
    _STATUS_TERMS = {"state", "status", "priority", "severity", "stage", "phase"}
    _CATEGORY_RULES = (
        (
            "Core Operations",
            {
                "case",
                "cases",
                "dispatch",
                "event",
                "events",
                "incident",
                "incidents",
                "job",
                "jobs",
                "order",
                "orders",
                "request",
                "requests",
                "schedule",
                "schedules",
                "service",
                "task",
                "tasks",
                "ticket",
                "tickets",
                "transaction",
                "transactions",
                "trip",
                "trips",
                "visit",
                "visits",
                "work",
            },
        ),
        (
            "People & Teams",
            {
                "assignee",
                "employee",
                "employees",
                "member",
                "members",
                "operator",
                "operators",
                "people",
                "person",
                "staff",
                "team",
                "teams",
                "technician",
                "technicians",
                "user",
                "users",
            },
        ),
        (
            "Locations & Facilities",
            {
                "address",
                "addresses",
                "branch",
                "branches",
                "building",
                "buildings",
                "facility",
                "facilities",
                "location",
                "locations",
                "region",
                "regions",
                "site",
                "sites",
                "warehouse",
                "warehouses",
                "zone",
                "zones",
            },
        ),
        (
            "Customers & Accounts",
            {
                "account",
                "accounts",
                "client",
                "clients",
                "company",
                "companies",
                "contact",
                "contacts",
                "customer",
                "customers",
                "partner",
                "partners",
                "supplier",
                "suppliers",
                "vendor",
                "vendors",
            },
        ),
        (
            "Assets & Inventory",
            {
                "asset",
                "assets",
                "catalog",
                "catalogs",
                "equipment",
                "inventory",
                "item",
                "items",
                "machine",
                "machines",
                "material",
                "materials",
                "part",
                "parts",
                "product",
                "products",
                "sku",
                "stock",
            },
        ),
        (
            "Finance & Billing",
            {
                "amount",
                "amounts",
                "bill",
                "billing",
                "budget",
                "budgets",
                "cost",
                "costs",
                "expense",
                "expenses",
                "finance",
                "financial",
                "invoice",
                "invoices",
                "payment",
                "payments",
                "price",
                "prices",
                "quote",
                "quotes",
                "revenue",
                "tax",
                "taxes",
            },
        ),
        (
            "Reference Data",
            {
                "category",
                "categories",
                "code",
                "codes",
                "dictionary",
                "enum",
                "label",
                "labels",
                "lookup",
                "master",
                "reference",
                "setting",
                "settings",
                "status",
                "statuses",
                "type",
                "types",
            },
        ),
    )

    def __init__(self, schema_service: Any | None = None) -> None:
        self.schema_service = schema_service

    def build(self, request: SimpleOnboardingRequest) -> SimpleOnboardingResponse:
        if self.schema_service is None:
            raise ValueError("Schema service is not available")
        snapshot = self._introspect_schema(request.db_url)
        return self.build_from_snapshot(snapshot, request)

    def build_from_snapshot(
        self,
        snapshot: Dict[str, Any],
        request: SimpleOnboardingRequest,
    ) -> SimpleOnboardingResponse:
        profiles = self._profiles_from_snapshot(snapshot)
        if not profiles:
            raise ValueError("No tables found in the target database")

        categories: Dict[str, str] = {}
        descriptions: Dict[str, str] = {}
        scores: Dict[str, int] = {}
        suggested_selection: Dict[str, bool] = {}
        selection_reasons: Dict[str, str] = {}

        for profile in profiles:
            category = self._categorize(profile)
            description = self._describe(profile, category)
            score = self._business_score(profile, category)
            suggested = self._suggest_selection(profile, category, score, request.selection_mode)

            categories[profile.name] = category
            descriptions[profile.name] = description
            scores[profile.name] = score
            suggested_selection[profile.name] = suggested
            selection_reasons[profile.name] = self._selection_reason(profile, category, suggested)

        selected_set = self._resolve_selected_tables(
            profiles=profiles,
            categories=categories,
            suggested_selection=suggested_selection,
            request=request,
        )

        relationships = self._relationships_for(profiles, selected_set)
        selected_tables = sorted(selected_set)
        ignored_tables = sorted(profile.name for profile in profiles if profile.name not in selected_set)

        category_groups = self._group_tables(
            [profile.name for profile in profiles],
            categories,
        )
        selected_category_groups = self._group_tables(selected_tables, categories)

        tables = [
            SimpleOnboardingTable(
                name=profile.name,
                category=categories[profile.name],
                description=descriptions[profile.name],
                selected=profile.name in selected_set,
                suggested_action="select" if suggested_selection[profile.name] else "ignore",
                selection_reason=selection_reasons[profile.name],
                business_score=scores[profile.name],
                columns=profile.column_names,
                related_tables=profile.related_tables,
            )
            for profile in sorted(profiles, key=lambda item: item.name)
        ]

        artifact = SimpleOnboardingArtifact(
            categories=selected_category_groups,
            selected_tables=selected_tables,
            table_descriptions={
                table_name: descriptions[table_name]
                for table_name in selected_tables
            },
            relationships=relationships,
            business_context=str(request.business_context or "").strip(),
            metrics=[],
        )

        return SimpleOnboardingResponse(
            database_target=str(snapshot.get("database_target") or ""),
            total_tables=len(profiles),
            selection_mode=request.selection_mode,
            categories=category_groups,
            selected_tables=selected_tables,
            ignored_tables=ignored_tables,
            tables=tables,
            artifact=artifact,
        )

    def _introspect_schema(self, db_url: str | None) -> Dict[str, Any]:
        target_url = str(db_url or getattr(self.schema_service, "default_db_url", "") or "").strip()
        engine = self.schema_service.get_engine_for_url(db_url)
        with engine.connect() as conn:
            inspector = inspect(conn)
            table_names = sorted(inspector.get_table_names())
            tables: List[Dict[str, Any]] = []
            for table_name in table_names:
                columns = inspector.get_columns(table_name) or []
                primary_key = inspector.get_pk_constraint(table_name) or {}
                foreign_keys = inspector.get_foreign_keys(table_name) or []
                tables.append(
                    {
                        "name": str(table_name).strip(),
                        "columns": [
                            {
                                "name": str(column.get("name") or "").strip(),
                                "type": str(column.get("type") or "").strip(),
                                "nullable": bool(column.get("nullable", True)),
                            }
                            for column in columns
                            if str(column.get("name") or "").strip()
                        ],
                        "primary_key": [
                            str(column).strip()
                            for column in (primary_key.get("constrained_columns") or [])
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
                    }
                )
        return {
            "database_target": SchemaService._safe_db_target(target_url),
            "table_count": len(tables),
            "tables": tables,
        }

    def _profiles_from_snapshot(self, snapshot: Dict[str, Any]) -> List[_TableProfile]:
        raw_tables = [table for table in (snapshot.get("tables") or []) if isinstance(table, dict)]
        profiles: List[_TableProfile] = []
        profile_by_name: Dict[str, _TableProfile] = {}

        for table in raw_tables:
            name = str(table.get("name") or "").strip()
            if not name:
                continue
            profile = _TableProfile(
                name=name,
                columns=[
                    dict(column)
                    for column in (table.get("columns") or [])
                    if isinstance(column, dict)
                ],
                primary_key=[
                    str(column).strip()
                    for column in (table.get("primary_key") or [])
                    if str(column).strip()
                ],
                foreign_keys=[
                    dict(foreign_key)
                    for foreign_key in (table.get("foreign_keys") or [])
                    if isinstance(foreign_key, dict)
                ],
            )
            profiles.append(profile)
            profile_by_name[profile.name] = profile

        for profile in profiles:
            for foreign_key in profile.foreign_keys:
                referred_table = str(foreign_key.get("referred_table") or "").strip()
                referred_profile = profile_by_name.get(referred_table)
                if referred_profile is None:
                    continue
                referred_profile.incoming_tables.append(profile.name)

        for profile in profiles:
            profile.incoming_tables = sorted(_dedupe_keep_order(profile.incoming_tables))

        return sorted(profiles, key=lambda item: item.name)

    def _categorize(self, profile: _TableProfile) -> str:
        if self._is_noise(profile):
            return "System Records"
        if self._is_bridge(profile):
            return "Reference Data"

        table_tokens = _tokens(profile.name)
        column_tokens = _tokens(*profile.column_names)
        best_category = ""
        best_score = -1

        for category, keywords in self._CATEGORY_RULES:
            score = (len(table_tokens & keywords) * 4) + len(column_tokens & keywords)
            if category == "Reference Data" and self._is_lookup(profile):
                score += 3
            if score > best_score:
                best_score = score
                best_category = category

        if best_score > 0:
            return best_category
        if self._is_lookup(profile) or self._is_bridge(profile):
            return "Reference Data"
        return "Core Operations"

    def _describe(self, profile: _TableProfile, category: str) -> str:
        label = _humanize(profile.name)
        business_columns = [_humanize(column) for column in self._business_columns(profile)[:3]]
        related_tables = [_humanize(table_name) for table_name in profile.related_tables[:2]]

        if self._is_noise(profile):
            return "System-generated records that are usually safe to ignore for business Q&A."
        if self._is_bridge(profile) and len(related_tables) >= 2:
            return f"Connects {related_tables[0]} and {related_tables[1]} records."
        if category == "People & Teams":
            return "Stores people and team records used for ownership, assignment, or approvals."
        if category == "Locations & Facilities":
            return "Stores site or location records used to place operational activity."
        if category == "Customers & Accounts":
            return "Stores customer, company, or account records used across business workflows."
        if category == "Assets & Inventory":
            return "Tracks assets, inventory, or product records used by operational teams."
        if category == "Finance & Billing":
            return "Tracks billing, cost, payment, or other financial records."
        if category == "Reference Data":
            return f"Reference data for {label} used to classify other business records."
        if business_columns:
            details = _join_natural(business_columns)
            return f"Tracks {label} records, including {details}."
        if related_tables:
            return f"Tracks {label} records linked to {_join_natural(related_tables)}."
        return f"Tracks {label} records for day-to-day business activity."

    def _business_score(self, profile: _TableProfile, category: str) -> int:
        if self._is_noise(profile):
            return 0

        score = 18
        if category == "Core Operations":
            score += 38
        elif category == "Reference Data":
            score += 12
        else:
            score += 26

        if self._is_lookup(profile):
            score += 5
        if self._is_bridge(profile):
            score -= 8

        score += min(16, len(profile.foreign_keys) * 4)
        score += min(16, len(profile.incoming_tables) * 4)
        score += min(12, len(self._business_columns(profile)) * 2)

        if self._has_semantic_columns(profile):
            score += 8

        return max(0, min(100, score))

    def _suggest_selection(
        self,
        profile: _TableProfile,
        category: str,
        score: int,
        selection_mode: str,
    ) -> bool:
        if self._is_noise(profile):
            return False

        threshold = 32 if selection_mode == "review" else 48
        if category == "Reference Data" and len(profile.incoming_tables) >= 1:
            threshold -= 6
        if self._is_bridge(profile) and selection_mode == "ai":
            threshold += 6
        return score >= threshold

    def _selection_reason(self, profile: _TableProfile, category: str, suggested: bool) -> str:
        if self._is_noise(profile):
            return "Looks like system, audit, migration, or temporary data."

        reasons: List[str] = []
        if category == "Core Operations":
            reasons.append("Looks like a main business workflow table.")
        elif category == "Reference Data":
            reasons.append("Looks like supporting reference data.")
        else:
            reasons.append(f"Fits the {category.lower()} group.")

        if profile.incoming_tables:
            reasons.append("Referenced by other business tables.")
        if self._is_bridge(profile):
            reasons.append("Mostly links records between tables.")
        elif self._business_columns(profile):
            preview = _join_natural([_humanize(column) for column in self._business_columns(profile)[:2]])
            reasons.append(f"Has business fields such as {preview}.")

        if not suggested:
            reasons.append("Recommended to ignore by the current selection mode.")
        return " ".join(reasons)

    def _resolve_selected_tables(
        self,
        *,
        profiles: List[_TableProfile],
        categories: Dict[str, str],
        suggested_selection: Dict[str, bool],
        request: SimpleOnboardingRequest,
    ) -> set[str]:
        known_tables = {profile.name for profile in profiles}

        if request.selected_tables:
            return {
                table_name
                for table_name in request.selected_tables
                if table_name in known_tables
            }

        selected = {
            table_name
            for table_name, should_select in suggested_selection.items()
            if should_select
        }

        include_categories = {name.lower() for name in request.include_categories}
        exclude_categories = {name.lower() for name in request.exclude_categories}

        for table_name, category in categories.items():
            normalized_category = category.lower()
            if normalized_category in include_categories:
                selected.add(table_name)
            if normalized_category in exclude_categories and table_name in selected:
                selected.remove(table_name)

        for pattern in request.bulk_include_patterns:
            selected.update(self._match_patterns(known_tables, pattern))
        for pattern in request.bulk_exclude_patterns:
            selected.difference_update(self._match_patterns(known_tables, pattern))

        selected.update(table_name for table_name in request.include_tables if table_name in known_tables)
        selected.difference_update(table_name for table_name in request.exclude_tables if table_name in known_tables)

        return selected

    @staticmethod
    def _match_patterns(table_names: Iterable[str], pattern: str) -> set[str]:
        normalized = str(pattern or "").strip().lower()
        if not normalized:
            return set()
        if not any(token in normalized for token in "*?[]"):
            normalized = f"*{normalized}*"
        return {
            table_name
            for table_name in table_names
            if fnmatch.fnmatch(table_name.lower(), normalized)
        }

    def _relationships_for(
        self,
        profiles: List[_TableProfile],
        selected_tables: set[str],
    ) -> List[SimpleOnboardingRelationship]:
        relationships: List[SimpleOnboardingRelationship] = []
        seen: set[tuple[str, tuple[str, ...], str, tuple[str, ...]]] = set()

        for profile in profiles:
            if profile.name not in selected_tables:
                continue
            for foreign_key in profile.foreign_keys:
                referred_table = str(foreign_key.get("referred_table") or "").strip()
                if referred_table not in selected_tables:
                    continue
                constrained_columns = _dedupe_keep_order(
                    str(column).strip()
                    for column in (foreign_key.get("constrained_columns") or [])
                )
                referred_columns = _dedupe_keep_order(
                    str(column).strip()
                    for column in (foreign_key.get("referred_columns") or [])
                )
                key = (
                    profile.name,
                    tuple(constrained_columns),
                    referred_table,
                    tuple(referred_columns),
                )
                if key in seen:
                    continue
                seen.add(key)
                relationships.append(
                    SimpleOnboardingRelationship(
                        from_table=profile.name,
                        from_columns=constrained_columns,
                        to_table=referred_table,
                        to_columns=referred_columns,
                    )
                )

        relationships.sort(
            key=lambda item: (
                item.from_table,
                item.to_table,
                ",".join(item.from_columns),
            )
        )
        return relationships

    @staticmethod
    def _group_tables(table_names: List[str], categories: Dict[str, str]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = defaultdict(list)
        for table_name in sorted(table_names):
            category = categories.get(table_name)
            if not category:
                continue
            grouped[category].append(table_name)
        return {category: names for category, names in sorted(grouped.items())}

    def _is_noise(self, profile: _TableProfile) -> bool:
        tokens = _tokens(profile.name)
        return bool(tokens & self._NOISE_TERMS) or profile.name.lower().startswith(("tmp_", "temp_"))

    def _is_bridge(self, profile: _TableProfile) -> bool:
        tokens = _tokens(profile.name)
        if tokens & self._BRIDGE_TERMS:
            return True
        return len(profile.foreign_keys) >= 2 and len(self._business_columns(profile)) <= 2 and len(profile.columns) <= 8

    def _is_lookup(self, profile: _TableProfile) -> bool:
        if self._is_noise(profile) or self._is_bridge(profile):
            return False
        names = {column.lower() for column in profile.column_names}
        key_columns = {"name", "label", "code", "title", "type", "category", "status"}
        if len(profile.columns) <= 5 and names & key_columns:
            return True
        return len(profile.columns) <= 4 and len(profile.incoming_tables) >= 1 and not profile.foreign_keys

    def _business_columns(self, profile: _TableProfile) -> List[str]:
        columns: List[str] = []
        for name in profile.column_names:
            lowered = name.lower()
            if lowered == "id" or lowered.endswith("_id"):
                continue
            if lowered in self._HOUSEKEEPING_COLUMNS:
                continue
            columns.append(name)
        return columns

    def _has_semantic_columns(self, profile: _TableProfile) -> bool:
        column_tokens = _tokens(*profile.column_names)
        return bool(column_tokens & self._DATE_TERMS) or bool(column_tokens & self._STATUS_TERMS)
