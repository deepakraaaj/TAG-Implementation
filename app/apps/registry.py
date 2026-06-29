from __future__ import annotations

import os
from pathlib import Path
from string import Template
from typing import Any, Optional

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, model_validator


class AppAuthConfig(BaseModel):
    """Per-tenant JWT verification contract.

    The host services (VTS, FITS, ...) already mint signed JWTs. This describes
    how to verify the token for one app: which env var holds the signing secret,
    how that secret is encoded, the allowed algorithms, and the claim names —
    because the tenants do not agree on a single claim shape:

      * VTS  (jjwt):  secret is **base64**, HS512, claim *values* are base64
                      (Transformer.fetchEncodedString), roles in ``authorities``
                      as a base64 CSV, tenant in plaintext ``loginFrom``.
      * FITS (nimbus): secret is **base64**, HS256, claims are plain,
                      company in ``cid``, roles in ``roles`` as a JSON list.
                      Tokens minted by a shared auth service bind the app via
                      a signed app claim such as ``appcode``, not the shared
                      service's ``loginFrom`` value.

    When ``enforce`` is true the endpoint refuses any request to this app that
    does not carry a valid signed token (fail closed).
    """

    enforce: bool = False
    # Name of the environment variable holding the signing secret. The secret is
    # NEVER stored in YAML — only its env var name is.
    secret_env: Optional[str] = None
    # How the env secret is turned into HMAC key bytes: "raw" (utf-8 bytes) or
    # "base64" (the secret string is base64-decoded first, like jjwt).
    secret_encoding: str = "raw"
    algorithms: list[str] = Field(default_factory=lambda: ["HS256"])
    leeway_seconds: int = 30
    issuer: Optional[str] = None
    audience: Optional[str] = None
    # Claim names.
    # Optional signed claim used to select/bind the app before trusting
    # tenant_claim. Example: FITS/REMP central auth emits appcode=REMP while
    # loginFrom=ALSISS only identifies the auth service.
    app_claim: Optional[str] = None
    tenant_claim: str = "loginFrom"
    user_id_claim: str = "userId"
    company_id_claim: str = "companyId"
    company_name_claim: Optional[str] = "companyName"
    roles_claim: str = "roles"
    user_name_claim: Optional[str] = "sub"
    # When set ("base64"), individual claim *values* are decoded with this codec
    # before use (VTS Transformer encoding). ``tenant_claim`` is always plaintext.
    claim_value_encoding: Optional[str] = None
    # How the roles claim is shaped once decoded: "list" (JSON array) or "csv".
    roles_format: str = "list"


class AppConfig(BaseModel):
    # Only `database_url` is required. Everything else has a sensible default,
    # so an app can be declared as just `name: <db_url>` (see AppsRegistryPayload).
    database_url: str
    display_name: Optional[str] = None
    domain: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    # Host-app identifiers (the JWT `loginFrom` value). Usually unnecessary —
    # loginFrom is auto-normalized to the app id (VTSDMS -> vts).
    login_from: list[str] = Field(default_factory=list)
    default_metadata: dict[str, Any] = Field(default_factory=dict)
    allow_mutations: bool = False
    require_select_where: bool = True
    # Empty = no per-app allow-list. System and sensitive tables/columns are
    # still blocked globally by SQLValidatorService.
    allowed_tables: list[str] = Field(default_factory=list)
    protected_tables: list[str] = Field(default_factory=list)
    # Per-tenant JWT verification contract. Absent = no auth enforced for this app.
    auth: Optional[AppAuthConfig] = None

    @property
    def domain_name(self) -> str:
        candidate = str(self.domain or self.name or "").strip()
        return candidate or ""


def _normalize_login_from(value: str | None) -> str:
    """Reduce a host-app identifier to a comparable stem.

    Strips non-alphanumerics, a leading ``als`` product prefix, and trailing
    ``dms``/``app`` channel suffixes so that ``VTSDMS``, ``VTSAPP`` and
    ``ALSVTS`` all normalize to ``vts``.
    """
    stem = "".join(ch for ch in str(value or "").casefold() if ch.isalnum())
    if stem.startswith("als") and len(stem) > 3:
        stem = stem[3:]
    for suffix in ("dms", "app"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
    return stem


class AppsRegistryPayload(BaseModel):
    apps: dict[str, AppConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_apps(cls, data: Any) -> Any:
        """Accept a flat mapping where each app is just `name: <db_url>`.

            apps:
              vts: ${VTS_DATABASE_URL}
              fits: ${REMP_DATABASE_URL}

        Full object form still works. `name`/`domain`/`display_name` default
        to the app id when omitted.
        """
        if not isinstance(data, dict):
            return data
        apps = data.get("apps")
        if not isinstance(apps, dict):
            return data
        normalized: dict[str, Any] = {}
        for app_id, value in apps.items():
            key = str(app_id or "").strip()
            if not key:
                continue
            entry = {"database_url": value} if isinstance(value, str) else dict(value or {})
            entry.setdefault("name", key)
            entry.setdefault("domain", key)
            entry.setdefault("display_name", key)
            normalized[key] = entry
        return {**data, "apps": normalized}


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

    @staticmethod
    def _clean_requested_app_id(app_id: str | None) -> str:
        return str(app_id or "").strip()

    def _canonical_app_id(self, app_id: str | None) -> str:
        key = self._clean_requested_app_id(app_id)
        if not key:
            return ""
        if key in self._apps:
            return key
        lowered = key.casefold()
        for candidate in self._apps:
            if candidate.casefold() == lowered:
                return candidate
        return key

    @classmethod
    def from_settings(cls, settings) -> "AppRegistry":
        raw_path = str(getattr(settings, "APPS_CONFIG_PATH", "") or "").strip()
        default_app_id = str(getattr(settings, "DEFAULT_CHAT_APP_ID", "") or "").strip() or None
        if not default_app_id:
            default_app_id = str(getattr(settings, "DOMAIN", "") or "").strip() or None
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
        key = self._canonical_app_id(app_id)
        if not key:
            raise KeyError("Application id is required.")
        if key not in self._apps:
            raise KeyError(f"Unknown application ID: {key}")
        return self._apps[key]

    def resolve_optional(self, app_id: str | None) -> Optional[AppConfig]:
        key = self._canonical_app_id(app_id)
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
        app_id = self._canonical_app_id(requested_app_id)
        if app_id:
            return app_id, self.resolve(app_id)
        return self.resolve_default()

    def resolve_alias(self, login_from: str | None) -> Optional[str]:
        """Map a JWT ``loginFrom`` value (e.g. ``VTSDMS``) to an app id.

        Resolution order: explicit ``login_from`` aliases, then a direct
        id/name match, then a normalized-stem match. Returns ``None`` when no
        app matches so callers can fall back to the default app.
        """
        raw = self._clean_requested_app_id(login_from)
        if not raw:
            return None
        lowered = raw.casefold()

        # 1. Explicit aliases declared in app config.
        for app_id, config in self._apps.items():
            for alias in config.login_from or []:
                if str(alias or "").strip().casefold() == lowered:
                    return app_id

        # 2. Direct match against an app id / name.
        canonical = self._canonical_app_id(raw)
        if canonical in self._apps:
            return canonical

        # 3. Normalized-stem match (VTSDMS -> vts).
        stem = _normalize_login_from(raw)
        if not stem:
            return None
        for app_id, config in self._apps.items():
            for candidate in (app_id, config.name or "", config.domain or ""):
                if _normalize_login_from(candidate) == stem:
                    return app_id
        return None

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
