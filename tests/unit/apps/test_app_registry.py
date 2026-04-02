from pathlib import Path

from app.apps import AppRegistry


class _Settings:
    def __init__(self, path: str, default_app_id: str | None = None) -> None:
        self.APPS_CONFIG_PATH = path
        self.DEFAULT_CHAT_APP_ID = default_app_id


def test_app_registry_loads_yaml_and_resolves_default(tmp_path: Path):
    config_path = tmp_path / "apps.local.yaml"
    config_path.write_text(
        """
apps:
  fits_dev_march_9:
    display_name: FITS
    domain: fits_dev_march_9
    database_url: mysql+aiomysql://localhost/fits
    default_metadata:
      company_id: 42
    allowed_tables: [task_transaction]
  vts:
    display_name: VTS
    database_url: mysql+aiomysql://localhost/VTS
    allowed_tables: [trip]
""".strip(),
        encoding="utf-8",
    )

    registry = AppRegistry.from_settings(_Settings(str(config_path), default_app_id="vts"))

    assert registry.enabled() is True
    app_id, config = registry.resolve_default()
    assert app_id == "vts"
    assert config.display_name == "VTS"
    assert registry.resolve("fits_dev_march_9").domain_name == "fits_dev_march_9"
    assert registry.resolve("fits_dev_march_9").default_metadata["company_id"] == 42


def test_app_registry_returns_empty_when_config_path_missing():
    registry = AppRegistry.from_settings(_Settings("/tmp/does-not-exist.yaml"))

    assert registry.enabled() is False
    app_id, config = registry.resolve_default()
    assert app_id is None
    assert config is None


def test_app_registry_expands_environment_variables_in_yaml(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "apps.docker.yaml"
    monkeypatch.setenv(
        "FITS_DATABASE_URL_DOCKER",
        "mysql+aiomysql://host.docker.internal:3306/fits_dev_march_9",
    )
    config_path.write_text(
        """
apps:
  fits_dev_march_9:
    display_name: FITS
    domain: fits_dev_march_9
    database_url: ${FITS_DATABASE_URL_DOCKER}
    allowed_tables: [task_transaction]
""".strip(),
        encoding="utf-8",
    )

    registry = AppRegistry.from_settings(_Settings(str(config_path)))

    assert registry.resolve("fits_dev_march_9").database_url == (
        "mysql+aiomysql://host.docker.internal:3306/fits_dev_march_9"
    )
