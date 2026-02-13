from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import os

class Settings(BaseSettings):
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str
    
    # LLM Configuration (Generic URL-based)
    LLM_API_KEY: Optional[str] = None
    LLM_BASE_URL: str = "https://api.groq.com/openai/v1" # Default to Groq for now
    LLM_MODEL: str = "llama-3.3-70b-versatile"
    LLM_TIMEOUT: int = 60  # Timeout in seconds for LLM API calls
    
    # Backwards compatibility (optional mapping)
    GROQ_API_KEY: Optional[str] = None

    
    # OpenAI for embeddings
    OPENAI_API_KEY: Optional[str] = None
    
    # Elasticsearch
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    class Config:
        env_file = os.path.join(os.path.dirname(__file__), "../.env")
        extra = "ignore"

@lru_cache()
def get_settings():
    s = Settings()
    # Auto-map legacy env vars if new ones are missing
    if not s.LLM_API_KEY and s.GROQ_API_KEY:
        s.LLM_API_KEY = s.GROQ_API_KEY
    return s
