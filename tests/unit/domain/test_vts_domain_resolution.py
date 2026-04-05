from app.domains.registry import DomainRegistry
import pytest

def test_vts_domain_config_conflicts(monkeypatch):
    monkeypatch.setenv("DOMAIN", "vts")
    # Reset singleton
    DomainRegistry._instance = None
    
    domain = DomainRegistry.get_current_domain()
    assert domain.name == "vts"
    
    # Check if critical fields match between layers (no warnings)
    entity_behavior = domain.get_entity_behavior_config()
    assert entity_behavior.get("status_filter_key") == "recent_state_id"
    assert entity_behavior.get("priority_filter_key") == "recent_state_id"
    assert entity_behavior.get("task_menu_today_value") == "scheduled_date=today"
    
    # Check enums
    assert domain.get_enum_mapping("recent_state_id", "Created") == 10
    assert domain.get_enum_label("recent_state_id", 30) == "En route"
