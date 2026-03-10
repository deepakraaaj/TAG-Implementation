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
