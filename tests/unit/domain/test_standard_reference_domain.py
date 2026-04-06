from app.domains.registry import DomainRegistry


def test_standard_reference_domain_loads_with_reports_and_no_conflicts():
    domain = DomainRegistry("standard_reference")

    assert domain.spec.domain.id == "standard_reference"
    assert domain.spec.semantics.primary_table == "work_item"
    assert "work_item_status_summary" in domain.get_config_section("reports")
    assert "report" in domain.spec.capabilities.routes
    assert "workflow" in domain.spec.capabilities.routes
    assert domain._load_diagnostics.get("conflicts") == []
