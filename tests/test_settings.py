"""Tests for Pydantic Settings (plan 01-04, GOAL-1.13)."""

from __future__ import annotations


def test_settings_loads_with_minimum_env() -> None:
    from app.config.settings import Settings

    s = Settings()
    assert s.app.env in ("dev", "staging", "prod")
    assert s.llm.model_conversation == "google/gemini-2.0-pro"
    assert s.llm.model_judge == "google/gemini-2.0-flash"
    assert s.openrouter.base_url == "https://openrouter.ai/api/v1"
    # SecretStr must never render the raw value via str()
    assert "test-key" not in str(s.openrouter.api_key)


def test_postgres_async_url_uses_asyncpg_driver() -> None:
    from app.config.settings import Settings

    s = Settings()
    assert s.postgres.async_url.startswith("postgresql+asyncpg://")
