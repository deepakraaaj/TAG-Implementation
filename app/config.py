from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Optional
import os

class Settings(BaseSettings):
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str
    
    # LLM Configuration (Generic URL-based)
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: str  # No default - must be set in .env
    LLM_MODEL: str  # No default - must be set in .env
    LLM_TIMEOUT: int = 60  # Timeout in seconds for LLM API calls
    LLM_MAX_RETRIES: int = 0  # Provider/client-level retries
    LLM_RETRY_ATTEMPTS: int = 1  # Application retry wrapper attempts
    LLM_RETRY_BACKOFF_SECONDS: float = 0.2
    
    # Backwards compatibility (optional mapping)
    GROQ_API_KEY: Optional[str] = None

    
    # OpenAI for embeddings
    OPENAI_API_KEY: Optional[str] = None
    

    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Guided flow execution mode (YAML only).
    ASSISTANT_FLOW_MODE: str = "yaml"

    # Production Settings - Phase 1
    QUERY_TIMEOUT_SECONDS: int = 30
    MAX_REPORT_ROWS: int = 10000
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 1000
    ENABLE_AUDIT_LOGGING: bool = False  # Set to True after running migration

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

@lru_cache()
def get_settings():
    s = Settings()
    # Auto-map legacy env vars if new ones are missing
    if not s.LLM_API_KEY and s.GROQ_API_KEY:
        s.LLM_API_KEY = s.GROQ_API_KEY
    return s
