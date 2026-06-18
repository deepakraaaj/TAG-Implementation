import asyncio
from pathlib import Path

import app.db.multi_tenant_manager as multi_tenant_manager


class _Settings:
    def __init__(self, path: str) -> None:
        self.APPS_CONFIG_PATH = path
        self.DEFAULT_CHAT_APP_ID = "crowd"


def _write_apps_config(path: Path) -> None:
    path.write_text(
        """
apps:
  crowd:
    display_name: Crowd
    domain: crowd
    database_url: mysql+aiomysql://localhost:3306/crowd
  REMP:
    display_name: FITS Dev March 9
    domain: REMP
    database_url: mysql+aiomysql://localhost:3306/REMP
""".strip(),
        encoding="utf-8",
    )


def test_multi_tenant_manager_resolves_database_url_from_app_registry(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "apps.remote.yaml"
    _write_apps_config(config_path)
    monkeypatch.setattr(multi_tenant_manager, "get_settings", lambda: _Settings(str(config_path)))

    assert (
        multi_tenant_manager.MultiTenantDatabaseManager.get_database_url("REMP")
        == "mysql+aiomysql://localhost:3306/REMP"
    )


def test_multi_tenant_manager_lists_display_names_from_app_registry(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "apps.remote.yaml"
    _write_apps_config(config_path)
    monkeypatch.setattr(multi_tenant_manager, "get_settings", lambda: _Settings(str(config_path)))

    databases = asyncio.run(multi_tenant_manager.MultiTenantDatabaseManager.list_available_databases())

    assert databases == {
        "crowd": "Crowd",
        "REMP": "FITS Dev March 9",
    }
