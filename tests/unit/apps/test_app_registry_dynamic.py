"""Unit tests for AppRegistry.dynamic_add."""
import pytest

from app.apps import AppRegistry


def test_dynamic_add_creates_and_resolves_app():
    registry = AppRegistry()
    config = registry.dynamic_add(
        app_id="demo",
        display_name="Demo DB",
        database_url="mysql+aiomysql://localhost/demo",
    )

    assert registry.enabled() is True
    assert config.display_name == "Demo DB"
    assert config.database_url == "mysql+aiomysql://localhost/demo"
    assert config.domain_name == "demo"

    resolved = registry.resolve("demo")
    assert resolved is config


def test_dynamic_add_with_explicit_domain():
    registry = AppRegistry()
    config = registry.dynamic_add(
        app_id="warehouse",
        display_name="Warehouse",
        database_url="mysql://localhost/wh",
        domain="wh_prod",
    )
    assert config.domain_name == "wh_prod"


def test_dynamic_add_empty_id_raises():
    registry = AppRegistry()
    with pytest.raises(ValueError, match="app_id is required"):
        registry.dynamic_add(app_id="", display_name="X", database_url="mysql://x")


def test_dynamic_add_default_resolution():
    registry = AppRegistry()
    registry.dynamic_add("alpha", "Alpha", "mysql://a")
    registry.dynamic_add("beta", "Beta", "mysql://b")

    app_id, config = registry.resolve_default()
    assert app_id == "alpha"  # alphabetical
    assert config is not None
