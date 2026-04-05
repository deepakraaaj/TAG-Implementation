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


def test_domain_registry_uses_settings_domain_when_os_env_is_unset(monkeypatch):
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.setattr(DomainRegistry, "_instance", None)
    monkeypatch.setattr(
        "app.domains.registry.get_settings",
        lambda: type("S", (), {"DOMAIN": "starter"})(),
    )

    domain = DomainRegistry.get_current_domain()

    assert domain.name == "starter"
