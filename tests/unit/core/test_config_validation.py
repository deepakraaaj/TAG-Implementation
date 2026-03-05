import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, Settings


def _base_settings_kwargs(tmp_path):
    return {
        "DATABASE_URL": "sqlite:///tmp/tag-test.sqlite3",
        "LLM_BASE_URL": "http://localhost:11434/v1",
        "LLM_MODEL": "test-model",
        "LLM_API_KEY": "",
        "REDIS_URL": "redis://localhost:6379",
        "EXPORT_TEMP_DIR": str((tmp_path / "exports").resolve()),
    }


def test_settings_reject_invalid_page_size_relationship(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            **_base_settings_kwargs(tmp_path),
            DEFAULT_PAGE_SIZE=200,
            MAX_PAGE_SIZE=100,
        )


def test_settings_require_redis_url_when_cache_enabled(tmp_path):
    kwargs = _base_settings_kwargs(tmp_path)
    kwargs["REDIS_URL"] = ""
    with pytest.raises(ValidationError):
        Settings(
            **kwargs,
            CACHE_ENABLED=True,
        )


def test_validate_runtime_rejects_relative_export_dir(tmp_path):
    kwargs = _base_settings_kwargs(tmp_path)
    kwargs["REDIS_URL"] = ""
    kwargs["EXPORT_TEMP_DIR"] = "relative/exports"
    settings = Settings(
        **kwargs,
        CACHE_ENABLED=False,
    )

    with pytest.raises(ConfigurationError):
        settings.validate_runtime()
