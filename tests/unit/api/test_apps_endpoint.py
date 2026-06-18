import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.api.v1.endpoints import apps as apps_endpoint
from app.apps.registry import AppConfig, AppRegistry
from app.domains.registry import DomainRegistry


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql):
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConnection(self._rows)


class _FakeSchemaService:
    def __init__(self, rows):
        self._rows = rows
        self.requested_urls = []

    def get_table_columns(self, _tables, db_url=None):
        self.requested_urls.append(db_url)
        return {"company": {"id", "name", "is_active"}}

    def get_engine_for_url(self, db_url):
        self.requested_urls.append(db_url)
        return _FakeEngine(self._rows)


def _request_with_container(container):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_list_apps_includes_default_company_id():
    registry = AppRegistry(
        {
            "REMP": AppConfig(
                display_name="FITS",
                database_url="mysql://demo/fits",
                domain="REMP",
                default_metadata={"company_id": 56942686},
            )
        },
        default_app_id="REMP",
    )
    req = _request_with_container(SimpleNamespace(app_registry=registry))

    payload = asyncio.run(apps_endpoint.list_apps(req))

    assert payload["default_app_id"] == "REMP"
    assert payload["apps"][0]["default_company_id"] == "56942686"


def test_list_app_companies_returns_sorted_company_payload():
    registry = AppRegistry(
        {
            "vts": AppConfig(
                display_name="VTS",
                database_url="mysql://demo/vts",
                domain="vts",
                default_metadata={"company_id": 56942673},
            )
        },
        default_app_id="vts",
    )
    schema_service = _FakeSchemaService(
        [
            {"company_id": 56942673, "company_name": "kritilabs vts", "is_active": 1},
            {"company_id": 56942677, "company_name": "Krithi Avadi", "is_active": 1},
        ]
    )
    req = _request_with_container(
        SimpleNamespace(
            app_registry=registry,
            schema_service=schema_service,
        )
    )

    payload = asyncio.run(apps_endpoint.list_app_companies("vts", req))

    assert payload["app_id"] == "vts"
    assert payload["default_company_id"] == "56942673"
    assert payload["companies"][0]["company_id"] == "56942673"
    assert payload["companies"][0]["company_name"] == "kritilabs vts"
    assert payload["companies"][0]["is_active"] is True


def test_get_app_domain_config_returns_effective_domain_summary(tmp_path, monkeypatch):
    root = tmp_path / "domains"
    starter = root / "starter"
    custom = root / "custom"

    _write_json(
        starter / "domain.json",
        {
            "name": "starter",
            "description": "Starter domain",
            "entity_behavior": {
                "primary_table": "work_item",
                "intent_mode": "auto",
                "primary_keywords": ["work item"],
                "primary_filter_keys": ["status"],
                "primary_label": "work items",
                "date_filter_keys": ["scheduled_date"],
                "status_filter_key": "status",
                "priority_filter_key": "priority",
                "date_phrase_map": {"today": "today"},
                "status_phrase_map": {"open": "Open"},
                "count_request_patterns": ["\\bcount\\b"],
                "user_filter_keys": ["assignee_id", "assignee"],
                "self_aliases": ["my"],
                "all_users_aliases": ["all users"],
                "default_entity_prompt": "mention entity",
                "filter_context_prompt": "mention entity then filter",
                "explicit_list_request_patterns": ["^\\s*show\\b"],
            },
            "user_lookup": {
                "table": "person",
                "id_column": "id",
                "first_name_column": "first_name",
                "last_name_column": "last_name",
                "tenant_column": "company_id",
                "metadata_key": "company_id",
                "filter_keys": ["assignee"],
                "canonical_filter_key": "assignee",
                "id_filter_key": "assignee_id",
                "search_limit": 12,
                "fallback_limit": 6,
                "fallback_name": "User",
            },
            "location_lookup": {
                "table": "location",
                "name_column": "name",
                "tenant_column": "company_id",
                "metadata_key": "company_id",
                "filter_keys": ["location_name"],
                "canonical_filter_key": "location_name",
                "id_filter_keys": ["location_id"],
                "search_limit": 12,
                "fuzzy_scan_limit": 200,
                "fallback_limit": 6,
            },
            "select_workflow": {
                "workflow_id": "select_filters",
                "workflow_ids": ["select_filters"],
                "state": "collect_filters",
                "mode": "menu",
                "next_field": "filters",
                "operation": "select",
            },
            "sql_builder": {
                "patterns": {
                    "direct_operation_patterns": ["^\\s*show\\b"],
                    "sql_statement_passthrough_pattern": "^SELECT\\b",
                    "sql_statement_guard_patterns": [{"start": "^SELECT\\b", "required": "\\bFROM\\b"}],
                    "forced_table_patterns": ["^\\s*show\\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)\\b"],
                    "pure_filter_query_patterns": ["\\s*status\\s*=\\s*.+\\s*"],
                    "task_for_clause_patterns": ["\\b{keyword}\\b\\s+for\\s+(.+)"],
                    "trailing_date_clause_pattern": "\\s+on\\s+\\d{4}-\\d{2}-\\d{2}$",
                },
                "heuristics": {
                    "llm_skip_short_query_length": 20,
                    "user_suggestion_candidate_pool_limit": 50,
                    "user_suggestion_min_score": 0.75,
                    "unfiltered_select_limit": 100,
                    "name_matching": {
                        "substring_min_length": 4,
                        "prefix_min_length": 3,
                        "meaningful_token_min_length": 2,
                        "ratio_threshold": 0.9,
                        "max_length_delta": 3,
                        "contains_score": 0.96,
                        "prefix_score": 0.85,
                    },
                },
                "ui": {
                    "filter_prompt_title_template": "Add filters for {table}",
                },
            },
        },
    )
    _write_json(
        starter / "schema_manifest.json",
        {
            "tables": {
                "work_item": {
                    "primary_key": "id",
                    "important_columns": {
                        "id": {"description": "Work item id"},
                    },
                }
            },
            "query_templates": {},
            "table_resolution_rules": [],
        },
    )
    _write_json(custom / "generated" / "domain.json", {"name": "custom", "description": "Custom domain"})
    _write_json(
        custom / "generated" / "entity_behavior.json",
        {
            "primary_table": "ticket",
            "primary_label": "tickets",
        },
    )
    _write_json(
        custom / "manual" / "entity_behavior.json",
        {
            "primary_table": "trip",
            "primary_label": "trips",
            "primary_menu_options": [{"label": "Today (trips)", "value": "scheduled_date=today"}],
        },
    )
    _write_json(
        custom / "generated" / "manifest" / "tables.json",
        {
            "ticket": {
                "primary_key": "id",
                "important_columns": {
                    "id": {"description": "Ticket id"},
                },
            }
        },
    )
    _write_json(
        custom / "manual" / "manifest" / "tables.json",
        {
            "trip": {
                "primary_key": "id",
                "important_columns": {
                    "id": {"description": "Trip id"},
                },
            }
        },
    )

    monkeypatch.setattr(DomainRegistry, "_domains_root_override", root)
    monkeypatch.setattr(DomainRegistry, "_instance", None)
    monkeypatch.setattr(DomainRegistry, "_instances", {})

    registry = AppRegistry(
        {
            "custom": AppConfig(
                display_name="Custom",
                database_url="sqlite:///custom.db",
                domain="custom",
            )
        },
        default_app_id="custom",
    )
    req = _request_with_container(SimpleNamespace(app_registry=registry))

    try:
        payload = asyncio.run(apps_endpoint.get_app_domain_config("custom", req))
    finally:
        monkeypatch.setattr(DomainRegistry, "_domains_root_override", None)
        monkeypatch.setattr(DomainRegistry, "_instance", None)
        monkeypatch.setattr(DomainRegistry, "_instances", {})

    assert payload["app_id"] == "custom"
    assert payload["domain_name"] == "custom"
    assert payload["effective_config"]["primary_table"] == "trip"
    assert payload["effective_config"]["primary_label"] == "trips"
    assert any(
        item["path"] == "entity_behavior.primary_table"
        for item in payload["effective_config"]["layer_diagnostics"]["conflicts"]
    )
