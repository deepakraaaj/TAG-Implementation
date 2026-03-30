import copy

import pytest

from app.domains.registry import DomainRegistry


def test_maintenance_domain_passes_config_validation():
    domain = DomainRegistry("maintenance")

    assert domain.get_config_section("sql_builder").get("heuristics", {}).get("unfiltered_select_limit") == 100
    assert "tables" in domain.manifest


def test_validate_domain_artifacts_rejects_missing_sql_builder_heuristics():
    domain = DomainRegistry("maintenance")
    config = copy.deepcopy(domain.config)
    manifest = copy.deepcopy(domain.manifest)

    config.get("sql_builder", {}).pop("heuristics", None)

    with pytest.raises(ValueError, match="sql_builder.heuristics"):
        DomainRegistry.validate_domain_artifacts(config, manifest, domain_name="maintenance")


def test_validate_domain_artifacts_rejects_invalid_user_match_score():
    domain = DomainRegistry("maintenance")
    config = copy.deepcopy(domain.config)
    manifest = copy.deepcopy(domain.manifest)

    config["sql_builder"]["heuristics"]["user_suggestion_min_score"] = 1.5

    with pytest.raises(ValueError, match="user_suggestion_min_score"):
        DomainRegistry.validate_domain_artifacts(config, manifest, domain_name="maintenance")


def test_build_domain_spec_returns_typed_spec():
    domain = DomainRegistry("maintenance")

    spec = DomainRegistry.build_domain_spec(domain.config, domain.manifest, domain_name="maintenance")

    assert spec.config.entity_behavior.primary_table == "task_transaction"
    assert "tables" in spec.manifest_dict()


def test_build_domain_spec_exposes_canonical_sections():
    domain = DomainRegistry("maintenance")

    spec = domain.spec

    assert spec.domain.id == "maintenance"
    assert spec.domain.name == "Kritibot"
    assert spec.schema_spec.tenant_scopes["task_transaction"] == "company_id"
    assert spec.semantics.primary_table == "task_transaction"
    assert "report" in spec.capabilities.routes
    assert "workflow" in spec.capabilities.routes
    assert "status_summary" in spec.capabilities.reports
    assert spec.language.response_templates["no_records_default"] == "No records found for the selected filters."
    assert "default_entity_prompt" in spec.ux.clarification_prompts
    assert "assistant_context" in spec.ux.disambiguation_rules


def test_domain_registry_exposes_canonical_section_accessor():
    domain = DomainRegistry("maintenance")

    capabilities = domain.get_canonical_section("capabilities")
    language = domain.get_canonical_section("language")

    assert "status_summary" in capabilities["reports"]
    assert "task_transaction" in language["labels"]["tables"]


def test_maintenance_cli_domain_stays_separate_from_curated_maintenance_behavior():
    domain = DomainRegistry("maintenance_cli")

    assert domain.config["flows_enabled"] == []
    assert domain.get_field_label("sche_details_id") == "sche_details_id"
    assert domain.get_enum_label("status", 1) == 1
    assert "status_summary" not in domain.get_config_section("reports")
    assert domain.is_flow_candidate("create a scheduled maintenance task", "scheduler_task_details") is False
