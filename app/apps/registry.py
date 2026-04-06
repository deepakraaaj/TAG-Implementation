from __future__ import annotations

import os
from pathlib import Path
from string import Template
from typing import Any, Optional

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    display_name: str
    database_url: str
    domain: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    default_metadata: dict[str, Any] = Field(default_factory=dict)
    allow_mutations: bool = False
    require_select_where: bool = True
    allowed_tables: list[str] = Field(default_factory=list)
    protected_tables: list[str] = Field(default_factory=list)

    @property
    def domain_name(self) -> str:
        candidate = str(self.domain or self.name or "").strip()
        return candidate or ""


class AppsRegistryPayload(BaseModel):
    apps: dict[str, AppConfig] = Field(default_factory=dict)


def _settings_env_values(settings: Any, repo_root: Path) -> dict[str, str]:
    env_values: dict[str, str] = {}

    model_config = getattr(settings, "model_config", None) or getattr(settings.__class__, "model_config", None) or {}
    raw_env_files = model_config.get("env_file") if isinstance(model_config, dict) else None
    if raw_env_files:
        env_files = raw_env_files if isinstance(raw_env_files, (list, tuple)) else [raw_env_files]
        for env_file in env_files:
            candidate = Path(str(env_file))
            if not candidate.is_absolute():
                candidate = repo_root / candidate
            if not candidate.exists():
                continue
            for key, value in dotenv_values(candidate).items():
                if key and value is not None:
                    env_values[str(key)] = str(value)

    for key, value in os.environ.items():
        if value is not None:
            env_values[str(key)] = str(value)

    return env_values


def _expand_env_placeholders(payload: Any, env_values: dict[str, str]) -> Any:
    if isinstance(payload, dict):
        return {
            key: _expand_env_placeholders(value, env_values)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_expand_env_placeholders(value, env_values) for value in payload]
    if isinstance(payload, str):
        return Template(payload).safe_substitute(env_values)
    return payload


class AppRegistry:
    def __init__(
        self,
        apps: Optional[dict[str, AppConfig]] = None,
        *,
        path: Optional[Path] = None,
        default_app_id: Optional[str] = None,
    ) -> None:
        self._apps = dict(apps or {})
        self.path = path
        self.default_app_id = str(default_app_id or "").strip() or None

    @classmethod
    def from_settings(cls, settings) -> "AppRegistry":
        raw_path = str(getattr(settings, "APPS_CONFIG_PATH", "") or "").strip()
        default_app_id = str(getattr(settings, "DEFAULT_CHAT_APP_ID", "") or "").strip() or None
        if not raw_path:
            return cls(default_app_id=default_app_id)

        repo_root = Path(__file__).resolve().parents[2]
        config_path = Path(raw_path)
        if not config_path.is_absolute():
            config_path = repo_root / config_path
        if not config_path.exists():
            return cls(path=config_path, default_app_id=default_app_id)

        with config_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        payload = _expand_env_placeholders(payload, _settings_env_values(settings, repo_root))
        parsed = AppsRegistryPayload.model_validate(payload)
        return cls(parsed.apps, path=config_path, default_app_id=default_app_id)

    def enabled(self) -> bool:
        return bool(self._apps)

    def list_apps(self) -> list[tuple[str, AppConfig]]:
        return sorted(self._apps.items(), key=lambda item: item[0])

    def resolve(self, app_id: str) -> AppConfig:
        key = str(app_id or "").strip()
        if not key:
            raise KeyError("Application id is required.")
        if key not in self._apps:
            raise KeyError(f"Unknown application ID: {key}")
        return self._apps[key]

    def resolve_optional(self, app_id: str | None) -> Optional[AppConfig]:
        key = str(app_id or "").strip()
        if not key:
            return None
        return self._apps.get(key)

    def resolve_default(self) -> tuple[str, AppConfig] | tuple[None, None]:
        if not self._apps:
            return None, None
        if self.default_app_id and self.default_app_id in self._apps:
            return self.default_app_id, self._apps[self.default_app_id]
        app_id = next(iter(sorted(self._apps.keys())))
        return app_id, self._apps[app_id]

    def resolve_request(self, requested_app_id: str | None) -> tuple[str | None, AppConfig | None]:
        if requested_app_id:
            return str(requested_app_id).strip(), self.resolve(requested_app_id)
        return self.resolve_default()

    def dynamic_add(
        self,
        app_id: str,
        display_name: str,
        database_url: str,
        *,
        domain: str | None = None,
        default_metadata: dict[str, Any] | None = None,
        allowed_tables: list[str] | None = None,
    ) -> AppConfig:
        """Register an application at runtime (no YAML file needed)."""
        key = str(app_id or "").strip()
        if not key:
            raise ValueError("app_id is required")
        config = AppConfig(
            display_name=display_name,
            database_url=database_url,
            domain=domain or key,
            default_metadata=default_metadata or {},
            allowed_tables=allowed_tables or [],
        )
        self._apps[key] = config
        return config
