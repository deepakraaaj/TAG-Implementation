from app.domains.registry import DomainRegistry


def test_maintenance_manifest_does_not_inherit_starter_tables(monkeypatch):
    monkeypatch.setenv("DOMAIN", "maintenance")
    DomainRegistry._instance = None

    domain = DomainRegistry.get_current_domain()
    tables = domain.manifest.get("tables", {})
    query_templates = domain.manifest.get("query_templates", {})

    assert "user" in tables
    assert "person" not in tables
    assert "user" in query_templates
    assert "person" not in query_templates
