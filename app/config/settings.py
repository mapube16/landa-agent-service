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

from pathlib import Path
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

    # Real OpenRouter slugs (verified against /api/v1/models 2026-06-28):
    # the original `google/gemini-2.0-pro` from CLAUDE.md was speculative
    # and OpenRouter rejects it with 400. Gemini 2.0 only ships Flash on
    # OpenRouter; the production "pro" line is 2.5+. Plan 01-05 deviation.
    model_conversation: str = "google/gemini-2.5-pro"
    model_judge: str = "google/gemini-2.5-flash"
    model_intent: str = "google/gemini-2.5-flash"
    model_summarizer: str = "google/gemini-2.5-flash"
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
    # Optional — Railway sometimes exposes a workspace_id; harmless if absent.
    # Captured here so future tracing-config code can read it without touching
    # os.getenv (Phase 1 follow-up, RESEARCH Pitfall 9).
    workspace_id: SecretStr | None = None


class WhatsAppSettings(BaseSettings):
    """Meta Cloud API credentials + echo allowlist (Phase 2, D-01/D-02/D-06/D-08/D-17)."""

    model_config = SettingsConfigDict(
        env_prefix="WA_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    token: SecretStr  # REQUIRED — Meta system user token
    phone_id: str  # REQUIRED — WhatsApp business phone id
    business_account_id: str | None = None  # informational only
    webhook_secret: SecretStr  # REQUIRED — HMAC X-Hub-Signature-256 (D-16)
    verify_token: SecretStr  # REQUIRED — GET challenge (D-17)
    # CSV env var ``WA_ECHO_ALLOWLIST=+1...,+2...`` parsed by ``_split_csv``
    # below (same trick as ``LLMSettings.fallbacks_conversation``).
    echo_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("echo_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept ``"a,b,c"`` env-var form and split into a list."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


class SoftSegurosSettings(BaseSettings):
    """SoftSeguros REST API credentials (Phase 2, D-01/D-13)."""

    model_config = SettingsConfigDict(
        env_prefix="SOFTSEGUROS_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    base_url: str = "https://app.softseguros.com/"
    username: SecretStr  # REQUIRED — credential to /api-token-auth/
    password: SecretStr  # REQUIRED


class ChatwootSettings(BaseSettings):
    """Chatwoot self-hosted inbox credentials (Phase 3, D-Claude-Discretion).

    Inbox is an "API Channel" type (confirmed in 03-00 probe, Task 2).
    Separate from the WhatsApp native inbox wired in F4.
    ``api_key`` is a Chatwoot user-level ``api_access_token`` (from Profile Settings),
    rendered ``**********`` in all repr/log output (SecretStr rule, CLAUDE.md).
    """

    model_config = SettingsConfigDict(
        env_prefix="CHATWOOT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    url: str = "https://chat.landatech.org"  # operator may override via CHATWOOT_URL
    api_key: SecretStr  # REQUIRED — user api_access_token from Chatwoot Profile
    account_id: int  # REQUIRED — visible in chat URL /app/accounts/N/...
    inbox_id: int  # REQUIRED — id of the API Channel inbox created in 03-00
    # Inbox-level enable_auto_assignment does NOT fire for API-channel
    # conversations, so we set assignee_id directly when this is configured.
    # Without it, new conversations land in "Unassigned" and never show up in
    # the agent's mobile app inbox.
    default_assignee_id: int | None = None
    # Phase 4 (D-15): shared secret for outbound webhook HMAC verification.
    # Set in Chatwoot admin → Settings → Integrations → Webhooks.
    webhook_secret: SecretStr  # REQUIRED (F4+) — CHATWOOT_WEBHOOK_SECRET


class PaymentSettings(BaseSettings):
    """Phase 4 payment flow settings (D-01, D-06, D-22).

    ``cartera_phone_allowlist_raw`` is read via top-level env var name
    ``CARTERA_PHONE_ALLOWLIST`` (not prefixed) because the operator stores it
    without a prefix per D-06. The ``cartera_phone_allowlist`` property
    parses it into a frozenset of E.164 strings.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAYMENT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Top-level env name (no prefix) — read via alias.
    cartera_phone_allowlist_raw: str = Field(default="", alias="CARTERA_PHONE_ALLOWLIST")
    # PAYMENT_VOLUME_PATH — path to Railway volume for comprobantes.
    volume_path: Path = Field(default=Path("/data/comprobantes"))
    # META_TEMPLATE_NO_ANSWER_NAME — template name for "no answer" flow (D-22).
    template_no_answer_name: str = Field(
        default="voice_no_answer_followup", alias="META_TEMPLATE_NO_ANSWER_NAME"
    )

    @property
    def cartera_phone_allowlist(self) -> frozenset[str]:
        """Parse CARTERA_PHONE_ALLOWLIST CSV into a frozenset of E.164 numbers.

        Silently drops entries that don't start with '+' (not E.164).
        """
        raw = self.cartera_phone_allowlist_raw
        if not raw:
            return frozenset()
        entries = (e.strip() for e in raw.split(","))
        return frozenset(e for e in entries if e.startswith("+"))


class AuditSettings(BaseSettings):
    """Phase 5 audit log settings (SEC-01, SEC-02).

    Controls the filesystem sink path (Railway volume mount from 04-01)
    and whether the sink is enabled. Sink is used by the ARQ cron job in
    05-03 to write daily ``.ndjson`` snapshots of the audit chain.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUDIT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # Default path is inside the Railway volume already mounted in 04-01.
    # No new mount needed — shares /data/comprobantes volume.
    sink_path: Path = Path("/data/comprobantes/audit")
    sink_enabled: bool = True


class RateLimitSettings(BaseSettings):
    """Phase 5 Redis sliding-window rate limiter settings (SEC-06).

    Three concentric limit levels (RESEARCH Pattern 5):
    - phone_limit:  per-WhatsApp-number limit in ``window_s`` seconds
    - poliza_limit: per-poliza limit in ``window_s`` seconds
    - global_limit: global throughput cap in ``window_s`` seconds
    """

    model_config = SettingsConfigDict(
        env_prefix="RATE_LIMIT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    enabled: bool = True
    phone_limit: int = 20  # messages per window per WhatsApp number
    poliza_limit: int = 10  # messages per window per poliza_id
    global_limit: int = 500  # total messages per window across all numbers
    window_s: int = 60  # sliding window size in seconds


class LambdaProyectSettings(BaseSettings):
    """Integration settings for the lambda-proyect voice agent (D-23).

    ``internal_token`` is the shared secret authenticating requests from
    lambda-proyect to ``POST /case/handoff/no_answer``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAMBDA_PROYECT_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    base_url: str = "http://localhost:8001"
    internal_token: SecretStr  # REQUIRED — LAMBDA_PROYECT_INTERNAL_TOKEN (VOICE calls US)
    # Optional (Fase 6): auth WA→VOICE for B1/B2 calls. None until ops configures it —
    # the client no-ops with a warning log rather than breaking the payment flow.
    wa_to_voice_token: SecretStr | None = None


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
    whatsapp: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
    softseguros: SoftSegurosSettings = Field(default_factory=SoftSegurosSettings)
    chatwoot: ChatwootSettings = Field(default_factory=ChatwootSettings)
    # Phase 4 (04-01): payment flow + lambda-proyect integration.
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    lambda_proyect: LambdaProyectSettings = Field(default_factory=LambdaProyectSettings)
    # Phase 5 (05-01): audit log + rate limiting (SEC-01..SEC-06).
    audit: AuditSettings = Field(default_factory=AuditSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)


# Singleton — fail-fast at import time if a REQUIRED env var is missing.
settings = Settings()


__all__ = [
    "AppSettings",
    "AuditSettings",
    "ChatwootSettings",
    "LLMSettings",
    "LambdaProyectSettings",
    "LangSmithSettings",
    "OpenRouterSettings",
    "PaymentSettings",
    "PostgresSettings",
    "RateLimitSettings",
    "RedisSettings",
    "SentrySettings",
    "Settings",
    "SoftSegurosSettings",
    "WhatsAppSettings",
    "settings",
]
