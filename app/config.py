from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when runtime settings are structurally invalid for startup."""


_ALLOWED_APP_ENVS = {"development", "test", "staging", "production"}
_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}

# Bundled self-signed TLS cert/key (cert/ at the repo root). These are committed
# so `git clone && python -m app.main` serves HTTPS on 443 out of the box. They
# are self-signed dev certs — replace with a real cert in production (and never
# commit a real private key).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SSL_CERTFILE = os.path.join(_PROJECT_ROOT, "cert", "kritilabs.cert")
_DEFAULT_SSL_KEYFILE = os.path.join(_PROJECT_ROOT, "cert", "kritilabs.pem")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_DEFAULT_MODEL = "gpt-oss-120b"


class Settings(BaseSettings):
    APP_ENV: str = "development"
    # Address the HTTP server binds to. Defaults match local/dev; DevOps can set
    # APP_PORT=443 (and mount TLS via the platform) at deploy time without code
    # changes. Binding 443 directly needs a privileged process
    # (root or CAP_NET_BIND_SERVICE).
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 443
    # TLS termination at the app (uvicorn) itself. Defaults to the bundled
    # self-signed cert/key in cert/ so a fresh clone serves HTTPS on 443 with no
    # extra setup. Override (or set to "" / empty) when a proxy/ingress
    # terminates TLS, or to point at a real cert. Both must be set together.
    APP_SSL_CERTFILE: Optional[str] = _DEFAULT_SSL_CERTFILE
    APP_SSL_KEYFILE: Optional[str] = _DEFAULT_SSL_KEYFILE
    LOG_LEVEL: str = "INFO"
    LOG_JSON: Optional[bool] = None
    DOMAIN: str = "REMP"
    DATABASE_URL: Optional[str] = None
    APPS_CONFIG_PATH: Optional[str] = None
    DEFAULT_CHAT_APP_ID: Optional[str] = None
    CORS_ORIGINS: Any = []
    CORS_ALLOW_ORIGIN_REGEX: Optional[str] = None
    CORS_ALLOW_CREDENTIALS: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    TRUST_PROXY_HEADERS: bool = False

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_CONNECT_RETRIES: int = 3
    DB_CONNECT_RETRY_BACKOFF_SECONDS: float = 1.0

    # TLS/SSL for database connections (applied via connect_args in app/db/dialect.py).
    # Off by default = plaintext (unchanged behaviour). Enable to encrypt the
    # connection to a TLS-requiring DB (managed/cloud MySQL or Postgres).
    DB_SSL_ENABLED: bool = False
    # Verify the server certificate. Default False matches the MySQL Connector/J
    # default the Java services rely on (encrypt, but don't verify the cert).
    # Set True together with DB_SSL_CA for full verification.
    DB_SSL_VERIFY_CERT: bool = False
    # Path to a CA certificate (PEM) the server cert must validate against. Only
    # needed for verified TLS; leave blank for encrypt-without-verify.
    DB_SSL_CA: str = ""

    STRICT_STARTUP_PROBES: Optional[bool] = None
    
    # LLM Configuration (Cerebras by default, OpenAI-compatible URL-based client)
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: str = CEREBRAS_BASE_URL
    LLM_MODEL: str = CEREBRAS_DEFAULT_MODEL
    LLM_TIMEOUT: int = 60  # Timeout in seconds for LLM API calls
    LLM_MAX_RETRIES: int = 0  # Provider/client-level retries
    # Total application-level attempts per LLM call. >1 lets a transient model/
    # network blip recover instead of surfacing a "connection issue" message.
    LLM_RETRY_ATTEMPTS: int = 3
    LLM_RETRY_BACKOFF_SECONDS: float = 0.5
    LLM_HEALTHCHECK_TIMEOUT_SECONDS: float = 5.0
    INTENT_DETECTION_TIMEOUT_SECONDS: float = 2.0
    
    # Backwards compatibility / provider-specific key aliases (optional mapping)
    CEREBRAS_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None

    
    # OpenAI for embeddings
    OPENAI_API_KEY: Optional[str] = None
    SEMANTIC_RETRIEVAL_ENABLED: bool = False
    SEMANTIC_RETRIEVAL_PROVIDER: str = "fastembed"
    SEMANTIC_RETRIEVAL_MODEL: str = "BAAI/bge-small-en-v1.5"
    SEMANTIC_RETRIEVAL_CHROMA_PATH: str = "./output/chromadb"
    SEMANTIC_RETRIEVAL_TOP_K: int = 6
    SEMANTIC_RETRIEVAL_PROMPT_K: int = 6
    SEMANTIC_RETRIEVAL_MIN_SCORE: float = 0.35
    SEMANTIC_RETRIEVAL_ROUTE_MIN_SCORE: float = 0.45
    SEMANTIC_RETRIEVAL_AUTO_LEARN_ON_SUCCESS: bool = False
    
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

    # Demo kill switch for the narrow task-status write path (REMP guided
    # "update <task> status=..." flow). OFF by default = fully read-only posture,
    # identical to having no write capability at all. Flip to True ONLY for a
    # controlled demo; set back to False to instantly revert. This gates the
    # server-side mutation hint, so when False no task-status UPDATE is ever
    # authored, regardless of message. Full auth/guardrail hardening is tracked
    # separately and should land before this is enabled outside a demo.
    ENABLE_TASK_STATUS_WRITE: bool = False

    # DB protection guardrails
    # Hard ceiling on rows any single SELECT may return. The validator injects /
    # clamps a LIMIT to this value, and the executor enforces it again at fetch
    # time as a memory backstop. Prevents bulk exfiltration / OOM.
    SQL_MAX_LIMIT: int = 1000
    # Per-statement execution timeout (ms) applied at the connection boundary so
    # a runaway query cannot pin the database.
    SQL_STATEMENT_TIMEOUT_MS: int = 30000
    # Deny-by-default table allowlist. When True, a query may only touch tables the
    # active domain manifest knows about (plus any per-app `allowed_tables`). Any
    # other table -- even if it exists in the DB -- is rejected. Blocks a
    # hallucinated/crafted query from reaching tables outside the assistant's scope.
    SQL_RESTRICT_TO_MANIFEST_TABLES: bool = True

    # NL->SQL audit trail. Emits a structured audit record (NL prompt, generated
    # SQL, principal, tenant/app, company, row count, status) to the application
    # log for every executed query. Log-based so it works with read-only DB
    # principals (no audit table write needed). On by default.
    AUDIT_NL2SQL_ENABLED: bool = True

    # Server-side iframe embedding allowlist. Space/comma-separated list of origins
    # permitted to frame this app (CSP `frame-ancestors`). Empty = deny all framing
    # (X-Frame-Options: DENY). Host wildcards allowed, e.g. https://*.kritilabs.com
    FRAME_ANCESTORS: Any = []

    # Admin dashboard
    # Static bearer token required by all /admin endpoints and the dashboard.
    # When empty, the admin surface is disabled entirely (fails closed).
    ADMIN_API_TOKEN: str = ""
    ADMIN_DASHBOARD_ENABLED: bool = True
    # Number of recent chat requests kept in the in-memory trace ring buffer.
    ADMIN_TRACE_BUFFER_SIZE: int = 200

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

    def get_apps_config_path(self) -> Optional[Path]:
        """Resolve APPS_CONFIG_PATH relative to project root if relative."""
        if not self.APPS_CONFIG_PATH:
            return None
        
        config_path = Path(self.APPS_CONFIG_PATH)
        if config_path.is_absolute():
            return config_path
        
        # Relative path - resolve from project root
        repo_root = Path(__file__).resolve().parents[1]  # Go up to project root
        return repo_root / config_path

    @field_validator("APP_ENV")
    def _validate_app_env(cls, value: str) -> str:
        candidate = str(value or "").strip().lower()
        if candidate not in _ALLOWED_APP_ENVS:
            raise ValueError(f"APP_ENV must be one of: {', '.join(sorted(_ALLOWED_APP_ENVS))}")
        return candidate

    @field_validator("LOG_LEVEL")
    def _validate_log_level(cls, value: str) -> str:
        candidate = str(value or "").strip().upper()
        if candidate not in _ALLOWED_LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(sorted(_ALLOWED_LOG_LEVELS))}")
        return candidate

    @field_validator("CORS_ORIGINS", mode="before")
    def _parse_cors_origins(cls, value: object) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return []
            if candidate.startswith("["):
                try:
                    parsed = json.loads(candidate)
                    values = parsed if isinstance(parsed, list) else [parsed]
                except (json.JSONDecodeError, TypeError):
                    # Fallback to single item if JSON parse fails but it looked like JSON
                    values = [candidate]
            else:
                values = candidate.split(",")
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = [str(value)]

        origins: list[str] = []
        for item in values:
            origin = str(item or "").strip().rstrip("/")
            if origin and origin not in origins:
                origins.append(origin)
        return origins

    @field_validator("CORS_ALLOW_ORIGIN_REGEX")
    def _validate_cors_origin_regex(cls, value: Optional[str]) -> Optional[str]:
        candidate = str(value or "").strip()
        return candidate or None

    @field_validator("FRAME_ANCESTORS", mode="before")
    def _parse_frame_ancestors(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            # Accept either comma- or space-separated origins (CSP uses spaces).
            values = str(value).replace(",", " ").split()
        ancestors: list[str] = []
        for item in values:
            origin = str(item or "").strip().rstrip("/")
            if origin and origin not in ancestors:
                ancestors.append(origin)
        return ancestors

    @field_validator("LLM_BASE_URL")
    def _validate_llm_base_url(cls, value: str) -> str:
        candidate = str(value or "").strip()
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LLM_BASE_URL must be a valid http(s) URL")
        return candidate.rstrip("/")

    @field_validator("LLM_MODEL")
    def _validate_llm_model(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("LLM_MODEL must not be empty")
        return candidate

    @field_validator("REDIS_URL")
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
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_POOL_TIMEOUT",
        "DB_POOL_RECYCLE",
        "DB_CONNECT_RETRIES",
        "RATE_LIMIT_PER_MINUTE",
        "CACHE_TTL_SECONDS",
        "CACHE_MAX_SIZE_MB",
        "EXPORT_MAX_ROWS",
        "SEMANTIC_RETRIEVAL_TOP_K",
        "SEMANTIC_RETRIEVAL_PROMPT_K",
    )
    def _validate_positive_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("must be greater than 0")
        return parsed

    @field_validator("LLM_MAX_RETRIES")
    def _validate_non_negative_int(cls, value: int) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError("must be greater than or equal to 0")
        return parsed

    @field_validator("LLM_RETRY_BACKOFF_SECONDS", "DB_CONNECT_RETRY_BACKOFF_SECONDS")
    def _validate_non_negative_float(cls, value: float) -> float:
        parsed = float(value)
        if parsed < 0:
            raise ValueError("must be greater than or equal to 0")
        return parsed

    @field_validator("SEMANTIC_RETRIEVAL_PROVIDER")
    def _validate_semantic_retrieval_provider(cls, value: str) -> str:
        candidate = str(value or "").strip().lower()
        if candidate not in {"fastembed", "chroma"}:
            raise ValueError("SEMANTIC_RETRIEVAL_PROVIDER must be 'fastembed' or 'chroma'")
        return candidate

    @field_validator("SEMANTIC_RETRIEVAL_MODEL")
    def _validate_semantic_retrieval_model(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("SEMANTIC_RETRIEVAL_MODEL must not be empty")
        return candidate

    @field_validator("SEMANTIC_RETRIEVAL_MIN_SCORE", "SEMANTIC_RETRIEVAL_ROUTE_MIN_SCORE")
    def _validate_probability_score(cls, value: float) -> float:
        parsed = float(value)
        if parsed < 0 or parsed > 1:
            raise ValueError("must be between 0 and 1")
        return parsed

    @field_validator("INTENT_DETECTION_TIMEOUT_SECONDS", "LLM_HEALTHCHECK_TIMEOUT_SECONDS")
    def _validate_positive_float(cls, value: float) -> float:
        parsed = float(value)
        if parsed <= 0:
            raise ValueError("must be greater than 0")
        return parsed

    @model_validator(mode="after")
    def _validate_consistency(self) -> "Settings":
        if self.LOG_JSON is None:
            self.LOG_JSON = self.APP_ENV == "production"
        if self.STRICT_STARTUP_PROBES is None:
            self.STRICT_STARTUP_PROBES = self.APP_ENV == "production"
        if not self.CORS_ORIGINS and self.APP_ENV != "production":
            self.CORS_ORIGINS = ["*"]
        if self.DEFAULT_PAGE_SIZE > self.MAX_PAGE_SIZE:
            raise ValueError("DEFAULT_PAGE_SIZE must be less than or equal to MAX_PAGE_SIZE")
        if self.CACHE_ENABLED and not str(self.REDIS_URL or "").strip():
            raise ValueError("REDIS_URL must be set when CACHE_ENABLED=true")
        if self.APP_ENV == "production":
            if not self.CORS_ORIGINS and not self.CORS_ALLOW_ORIGIN_REGEX:
                raise ValueError("CORS_ORIGINS or CORS_ALLOW_ORIGIN_REGEX must be set explicitly in production")
            if "*" in self.CORS_ORIGINS:
                raise ValueError("CORS_ORIGINS must not contain '*' in production")
        if self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ORIGINS and self.APP_ENV == "production":
            raise ValueError("Wildcard CORS origins cannot be used with credentials in production")
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
    # Auto-map provider-specific env vars if the generic key is missing.
    if not s.LLM_API_KEY and s.CEREBRAS_API_KEY:
        s.LLM_API_KEY = s.CEREBRAS_API_KEY
    elif not s.LLM_API_KEY and s.GROQ_API_KEY:
        s.LLM_API_KEY = s.GROQ_API_KEY
    if not s.APPS_CONFIG_PATH:
        s.APPS_CONFIG_PATH = str(os.getenv("TAG_FASTMCP_APPS_CONFIG_PATH") or "").strip() or None
    if not s.DEFAULT_CHAT_APP_ID:
        s.DEFAULT_CHAT_APP_ID = str(os.getenv("TAG_FASTMCP_DEFAULT_CHAT_APP_ID") or "").strip() or None
    return s
