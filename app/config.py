from functools import lru_cache
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when runtime settings are structurally invalid for startup."""


_ALLOWED_APP_ENVS = {"development", "test", "staging", "production"}
_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


class Settings(BaseSettings):
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DOMAIN: str = "maintenance"
    DATABASE_URL: str
    APPS_CONFIG_PATH: Optional[str] = None
    DEFAULT_CHAT_APP_ID: Optional[str] = None
    
    # LLM Configuration (Generic URL-based)
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: str  # No default - must be set in .env
    LLM_MODEL: str  # No default - must be set in .env
    LLM_TIMEOUT: int = 60  # Timeout in seconds for LLM API calls
    LLM_MAX_RETRIES: int = 0  # Provider/client-level retries
    LLM_RETRY_ATTEMPTS: int = 1  # Application retry wrapper attempts
    LLM_RETRY_BACKOFF_SECONDS: float = 0.2
    INTENT_DETECTION_TIMEOUT_SECONDS: float = 2.0
    
    # Backwards compatibility (optional mapping)
    GROQ_API_KEY: Optional[str] = None

    
    # OpenAI for embeddings
    OPENAI_API_KEY: Optional[str] = None
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Production Settings - Phase 1
    QUERY_TIMEOUT_SECONDS: int = 30
    MAX_REPORT_ROWS: int = 10000
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 1000
    ENABLE_AUDIT_LOGGING: bool = False  # Set to True after running migration
    MUTATION_ALLOWED_ROLES: str = "admin,superadmin"
    MUTATION_REQUIRE_EXPLICIT_PERMISSION: bool = True

    # Production Settings - Phase 2
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 300  # 5 minutes
    CACHE_MAX_SIZE_MB: int = 100
    EXPORT_MAX_ROWS: int = 50000
    EXPORT_TEMP_DIR: str = "/tmp/exports"
    METRICS_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "../.env"),
        extra="ignore",
    )

    @field_validator("APP_ENV")
    @classmethod
    def _validate_app_env(cls, value: str) -> str:
        candidate = str(value or "").strip().lower()
        if candidate not in _ALLOWED_APP_ENVS:
            raise ValueError(f"APP_ENV must be one of: {', '.join(sorted(_ALLOWED_APP_ENVS))}")
        return candidate

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        candidate = str(value or "").strip().upper()
        if candidate not in _ALLOWED_LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(_ALLOWED_LOG_LEVELS))}")
        return candidate

    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        candidate = str(value or "").strip()
        parsed = urlsplit(candidate)
        scheme = str(parsed.scheme or "").strip().lower()
        if not scheme:
            raise ValueError("DATABASE_URL must include a database scheme")
        if not any(token in scheme for token in ("sqlite", "mysql", "postgresql", "postgres")):
            raise ValueError("DATABASE_URL must use a supported sqlite/mysql/postgresql dialect")
        if "sqlite" not in scheme and not parsed.hostname:
            raise ValueError("DATABASE_URL must include a hostname for non-sqlite databases")
        return candidate

    @field_validator("LLM_BASE_URL")
    @classmethod
    def _validate_llm_base_url(cls, value: str) -> str:
        candidate = str(value or "").strip()
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LLM_BASE_URL must be a valid http(s) URL")
        return candidate.rstrip("/")

    @field_validator("LLM_MODEL")
    @classmethod
    def _validate_llm_model(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("LLM_MODEL must not be empty")
        return candidate

    @field_validator("REDIS_URL")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"redis", "rediss", "unix"}:
            raise ValueError("REDIS_URL must use redis://, rediss://, or unix://")
        if parsed.scheme != "unix" and not parsed.hostname:
            raise ValueError("REDIS_URL must include a hostname")
        return candidate

    @field_validator(
        "LLM_TIMEOUT",
        "LLM_RETRY_ATTEMPTS",
        "QUERY_TIMEOUT_SECONDS",
        "MAX_REPORT_ROWS",
        "DEFAULT_PAGE_SIZE",
        "MAX_PAGE_SIZE",
        "CACHE_TTL_SECONDS",
        "CACHE_MAX_SIZE_MB",
        "EXPORT_MAX_ROWS",
    )
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("must be greater than 0")
        return parsed

    @field_validator("LLM_MAX_RETRIES")
    @classmethod
    def _validate_non_negative_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError("must be greater than or equal to 0")
        return parsed

    @field_validator("LLM_RETRY_BACKOFF_SECONDS")
    @classmethod
    def _validate_non_negative_float(cls, value: float) -> float:
        parsed = float(value)
        if parsed < 0:
            raise ValueError("must be greater than or equal to 0")
        return parsed

    @field_validator("INTENT_DETECTION_TIMEOUT_SECONDS")
    @classmethod
    def _validate_positive_float(cls, value: float) -> float:
        parsed = float(value)
        if parsed <= 0:
            raise ValueError("must be greater than 0")
        return parsed

    @model_validator(mode="after")
    def _validate_consistency(self) -> "Settings":
        if self.DEFAULT_PAGE_SIZE > self.MAX_PAGE_SIZE:
            raise ValueError("DEFAULT_PAGE_SIZE must be less than or equal to MAX_PAGE_SIZE")
        if self.CACHE_ENABLED and not str(self.REDIS_URL or "").strip():
            raise ValueError("REDIS_URL must be set when CACHE_ENABLED=true")
        return self

    def validate_runtime(self) -> None:
        issues: list[str] = []

        export_dir = Path(self.EXPORT_TEMP_DIR).expanduser()
        if not export_dir.is_absolute():
            issues.append("EXPORT_TEMP_DIR must be an absolute path")
        else:
            try:
                export_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                issues.append(f"EXPORT_TEMP_DIR is not writable: {exc}")
            else:
                if not os.access(export_dir, os.W_OK | os.X_OK):
                    issues.append(f"EXPORT_TEMP_DIR is not writable: {export_dir}")

        if issues:
            raise ConfigurationError(
                "Runtime configuration validation failed:\n- " + "\n- ".join(issues)
            )


@lru_cache()
def get_settings():
    s = Settings()
    # Auto-map legacy env vars if new ones are missing
    if not s.LLM_API_KEY and s.GROQ_API_KEY:
        s.LLM_API_KEY = s.GROQ_API_KEY
    if not s.APPS_CONFIG_PATH:
        s.APPS_CONFIG_PATH = str(os.getenv("TAG_FASTMCP_APPS_CONFIG_PATH") or "").strip() or None
    if not s.DEFAULT_CHAT_APP_ID:
        s.DEFAULT_CHAT_APP_ID = str(os.getenv("TAG_FASTMCP_DEFAULT_CHAT_APP_ID") or "").strip() or None
    return s
