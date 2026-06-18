import pytest
from pydantic import ValidationError

from app.config import (
    CEREBRAS_BASE_URL,
    CEREBRAS_DEFAULT_MODEL,
    ConfigurationError,
    Settings,
    get_settings,
)


def _base_settings_kwargs(tmp_path):
    return {
        "DOMAIN": "vts",
        "DATABASE_URL": "sqlite:///tmp/tag-test.sqlite3",
        "LLM_BASE_URL": CEREBRAS_BASE_URL,
        "LLM_MODEL": CEREBRAS_DEFAULT_MODEL,
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


def test_settings_default_to_vts_domain(tmp_path):
    settings = Settings(**_base_settings_kwargs(tmp_path))

    assert settings.DOMAIN == "vts"


def test_settings_default_to_cerebras_llm(tmp_path):
    kwargs = _base_settings_kwargs(tmp_path)
    kwargs.pop("LLM_BASE_URL")
    kwargs.pop("LLM_MODEL")

    settings = Settings(**kwargs)

    assert settings.LLM_BASE_URL == CEREBRAS_BASE_URL
    assert settings.LLM_MODEL == CEREBRAS_DEFAULT_MODEL


def test_get_settings_maps_cerebras_api_key(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/tag-test.sqlite3")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("EXPORT_TEMP_DIR", str((tmp_path / "exports").resolve()))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.LLM_API_KEY == "csk-test"


def test_settings_reject_non_positive_intent_detection_timeout(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            **_base_settings_kwargs(tmp_path),
            INTENT_DETECTION_TIMEOUT_SECONDS=0,
        )


def test_settings_reject_invalid_semantic_retrieval_provider(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            **_base_settings_kwargs(tmp_path),
            SEMANTIC_RETRIEVAL_PROVIDER="unknown",
        )


def test_settings_reject_out_of_range_semantic_retrieval_score(tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            **_base_settings_kwargs(tmp_path),
            SEMANTIC_RETRIEVAL_MIN_SCORE=1.5,
        )
