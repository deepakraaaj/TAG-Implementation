from app.domains.registry import DomainRegistry


def test_domain_registry_use_domain_scopes_current_domain(monkeypatch):
    monkeypatch.setenv("DOMAIN", "maintenance")
    DomainRegistry._instance = None

    outside = DomainRegistry.get_current_domain()
    assert outside.name == "maintenance"

    with DomainRegistry.use_domain("starter") as scoped:
        assert scoped.name == "starter"
        assert DomainRegistry.get_current_domain().name == "starter"

    assert DomainRegistry.get_current_domain().name == "maintenance"
