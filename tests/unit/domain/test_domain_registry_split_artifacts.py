import json
from pathlib import Path

from app.domains.registry import DomainRegistry


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _starter_config() -> dict:
    return {
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
        },
    }


def _starter_manifest() -> dict:
    return {
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
    }


def test_domain_registry_loads_generated_and_manual_artifacts(tmp_path, monkeypatch):
    root = tmp_path / "domains"
    starter = root / "starter"
    custom = root / "custom"

    _write_json(starter / "domain.json", _starter_config())
    _write_json(starter / "schema_manifest.json", _starter_manifest())

    _write_json(
        custom / "generated" / "domain.json",
        {
            "name": "custom",
            "description": "Custom domain",
        },
    )
    _write_json(
        custom / "generated" / "entity_behavior.json",
        {
            "primary_table": "asset",
            "primary_label": "assets",
        },
    )
    _write_json(
        custom / "generated" / "sql_builder.json",
        {
            "heuristics": {
                "unfiltered_select_limit": 250,
            }
        },
    )
    _write_json(
        custom / "generated" / "manifest" / "tables.json",
        {
            "asset": {
                "primary_key": "id",
                "important_columns": {
                    "id": {"description": "Asset id"},
                },
            }
        },
    )
    _write_json(
        custom / "manual" / "entity_behavior.json",
        {
            "default_entity_prompt": "mention asset entity",
        },
    )
    _write_json(
        custom / "manual" / "manifest" / "query_templates.json",
        {
            "asset": {
                "list": "SELECT id FROM asset LIMIT 10;",
            }
        },
    )

    monkeypatch.setattr(DomainRegistry, "_domains_root_override", root)
    monkeypatch.setattr(DomainRegistry, "_instance", None)

    try:
        domain = DomainRegistry("custom")

        assert domain.config["name"] == "custom"
        assert domain.get_config_section("entity_behavior")["primary_table"] == "asset"
        assert domain.get_config_section("entity_behavior")["default_entity_prompt"] == "mention asset entity"
        assert domain.get_config_section("sql_builder")["heuristics"]["unfiltered_select_limit"] == 250
        assert "asset" in domain.manifest["tables"]
        assert domain.manifest["query_templates"]["asset"]["list"] == "SELECT id FROM asset LIMIT 10;"
        assert domain.spec.config.entity_behavior.primary_table == "asset"
    finally:
        monkeypatch.setattr(DomainRegistry, "_domains_root_override", None)
        monkeypatch.setattr(DomainRegistry, "_instance", None)
