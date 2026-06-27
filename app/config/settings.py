"""Centralised Pydantic Settings for landa-agent-service.

Layout (per CLAUDE.md + CONTEXT.md D-07): one ``BaseSettings`` subclass per
domain with ``env_prefix`` and ``extra="ignore"``, then a root ``Settings``
container that composes them. All credentials are wrapped in ``SecretStr``
(never plain ``str``) so accidental ``repr``/log dumps render ``**********``.

LangSmith tracing is activated by env vars (``LANGSMITH_TRACING=true``,
``LANGSMITH_API_KEY``, ``LANGSMITH_PROJECT``) which ``langchain`` reads
natively at import time — Settings only validates their presence.

NEVER use ``os.getenv`` elsewhere in the codebase; import ``settings`` here.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# ---------------------------------------------------------------------------
# Per-domain settings (env_prefix isolates env-var namespaces, per CLAUDE.md).
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """Top-level app metadata, no env_prefix (read APP_* globals)."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["dev", "staging", "prod"] = "dev"
    public_url: str = "http://localhost:8000"
    version: str = "0.1.0"
    log_level: str = "INFO"


class PostgresSettings(BaseSettings):
    """Application Postgres pool (asyncpg via SQLAlchemy 2.0)."""

    model_config = SettingsConfigDict(
        env_prefix="POSTGRES_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    url: SecretStr  # REQUIRED — no default. Format: postgresql://user:pass@host:5432/db

    @property
    def async_url(self) -> str:
        """Return URL with the ``postgresql+asyncpg://`` SQLAlchemy 2.0 driver scheme."""
        raw = self.url.get_secret_value()
        if raw.startswith("postgresql+asyncpg://"):
            return raw
        if raw.startswith("postgresql://"):
            return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        if raw.startswith("postgres://"):
            return raw.replace("postgres://", "postgresql+asyncpg://", 1)
        return raw


class RedisSettings(BaseSettings):
    """Redis pool (ARQ queue, SoftSeguros cache, rate-limit tokens, idempotency)."""

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    url: SecretStr  # REQUIRED — no default. Format: redis://default:pass@host:6379/0


class LLMSettings(BaseSettings):
    """Per-role LLM model mapping. Defaults per CLAUDE.md + CONTEXT.md."""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    model_conversation: str = "google/gemini-2.0-pro"
    model_judge: str = "google/gemini-2.0-flash"
    model_intent: str = "google/gemini-2.0-flash"
    model_summarizer: str = "google/gemini-2.0-flash"
    # ``NoDecode`` keeps pydantic-settings from JSON-decoding the env-var so
    # the validator below can split a plain ``"a,b,c"`` CSV. Without it,
    # pydantic-settings would try ``json.loads("a,b,c")`` and crash.
    fallbacks_conversation: Annotated[list[str], NoDecode] = Field(default_factory=list)
    fallbacks_judge: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("fallbacks_conversation", "fallbacks_judge", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept ``"a,b,c"`` env-var form and split into a list."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


class OpenRouterSettings(BaseSettings):
    """OpenRouter gateway credentials. All LLM calls flow through here (CLAUDE.md rule)."""

    model_config = SettingsConfigDict(
        env_prefix="OPENROUTER_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: SecretStr  # REQUIRED
    base_url: str = "https://openrouter.ai/api/v1"


class LangSmithSettings(BaseSettings):
    """LangSmith tracing. ``api_key`` optional in CI; required at runtime.

    ``langchain`` reads these env vars natively on import (no explicit
    init); Settings only validates their presence. Project name follows
    D-04 pattern ``landa-agent-{env}``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LANGSMITH_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    api_key: SecretStr | None = None
    project: str = "landa-agent-dev"
    tracing: bool = True
    endpoint: str = "https://api.smith.langchain.com"


class SentrySettings(BaseSettings):
    """Sentry error reporting. ``dsn=None`` disables Sentry (tests, CI without DSN)."""

    model_config = SettingsConfigDict(
        env_prefix="SENTRY_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    dsn: SecretStr | None = None
    traces_sample_rate: float = 0.1
    profiles_sample_rate: float = 0.0


# ---------------------------------------------------------------------------
# Root container — composes the per-domain settings via default_factory so
# each subclass independently reads its own env_prefix from .env / process env.
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Composite settings — instantiate once at import time.

    Import as ``from app.config.settings import settings`` everywhere.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)
    sentry: SentrySettings = Field(default_factory=SentrySettings)


# Singleton — fail-fast at import time if a REQUIRED env var is missing.
settings = Settings()


__all__ = [
    "AppSettings",
    "LLMSettings",
    "LangSmithSettings",
    "OpenRouterSettings",
    "PostgresSettings",
    "RedisSettings",
    "SentrySettings",
    "Settings",
    "settings",
]
