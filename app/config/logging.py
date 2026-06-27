"""structlog setup with PII redaction (key-name + phone regex) and JSON output.

Per CONTEXT.md D-07 + RESEARCH.md Pattern 3:
- Hybrid PII redaction (key-name set + targeted regex for phone numbers in free text)
- The ``redact_pii`` processor MUST run BEFORE the renderer
- stdlib bridge via ``ProcessorFormatter`` so uvicorn/SQLAlchemy logs flow through
  the same processor chain (single JSON stream on stdout for Railway logs)
- ``merge_contextvars`` picks up ``correlation_id`` injected by
  ``asgi-correlation-id`` middleware (wired in main.py — plan 01-04)
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars
from structlog.types import EventDict, WrappedLogger

# Key-name set: any event_dict key matching (case-insensitive) is replaced
# wholesale with "[REDACTED]". Curated for the LANDA threat model (DPG
# Seguros policy data, WhatsApp tokens, OpenRouter / LangSmith creds,
# generic financial PII like saldo / documento / cedula).
PII_KEYS: frozenset[str] = frozenset(
    {
        # Phone identifiers
        "phone",
        "phone_number",
        "wa_token",
        "wa_phone_id",
        "wa_webhook_secret",
        "wa_verify_token",
        # API keys / secrets
        "openrouter_api_key",
        "langsmith_api_key",
        "sentry_dsn",
        "chatwoot_api_key",
        "softseguros_username",
        "softseguros_password",
        "lambda_proyect_internal_token",
        "api_key",
        "secret",
        "token",
        "password",
        "authorization",
        # Financial / customer PII
        "saldo",
        "saldo_pendiente",
        "monto",
        "documento",
        "cedula",
        "credit_card",
        "cvv",
        "ssn",
    }
)

# Phone-number regex — matches international and local formats with separators.
# Examples that match: +584141234567, +1-555-867-5309, 549 1134 5678,
# (555) 555-5555 (without the parens, after digit-normalisation it still hits).
# We deliberately keep this narrow per CONTEXT.md "comprehensive PII redaction
# is deferred to F5"; key-name redaction is the primary defense.
PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")


def redact_pii(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """structlog processor: redact PII by key-name and scrub phone-shaped substrings.

    Walks the event_dict (including nested dicts) and:
      1. Replaces values whose key is in ``PII_KEYS`` (case-insensitive) with
         ``"[REDACTED]"``.
      2. For remaining string values, replaces phone-shaped substrings with
         ``"[REDACTED_PHONE]"``.

    MUST be inserted into the processor chain BEFORE any renderer / formatter
    so JSONRenderer / ConsoleRenderer never see raw PII (RESEARCH.md Pattern 3
    inline comment, T-01-05 in 01-02-PLAN.md threat register).
    """
    _redact_dict(event_dict)
    return event_dict


def _redact_dict(d: MutableMapping[str, Any]) -> None:
    """Recursive in-place redaction used by ``redact_pii``.

    Mutates ``d`` so structlog's ``EventDict`` (a ``MutableMapping``) is
    accepted directly without re-wrapping into a fresh ``dict``.
    """
    for k in list(d):
        if k.lower() in PII_KEYS:
            d[k] = "[REDACTED]"
    for k, v in d.items():
        if isinstance(v, str) and d[k] != "[REDACTED]":
            d[k] = PHONE_RE.sub("[REDACTED_PHONE]", v)
        elif isinstance(v, MutableMapping):
            _redact_dict(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, MutableMapping):
                    _redact_dict(item)


def configure_logging(log_level: str = "INFO", env: str = "dev") -> None:
    """Wire up structlog + stdlib so all logs flow through one processor chain.

    - In ``env="dev"`` with a TTY, uses ``ConsoleRenderer`` for human-readable
      coloured output; otherwise emits JSON for Railway/Sentry to consume.
    - Calls ``logging.getLogger().handlers.clear()`` before installing the
      single StreamHandler so uvicorn's preconfigured handler doesn't
      double-emit (a common cause of duplicate log lines in FastAPI apps).
    - Sets noisy loggers (uvicorn.access, httpx, httpcore) to WARNING to keep
      ``/health`` polling out of production logs (mitigates T-01-07 — uvicorn
      access logs would otherwise echo client phone numbers in URLs).
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        redact_pii,  # MUST come before any renderer
        structlog.processors.StackInfoRenderer(),
        structlog.processors.dict_tracebacks,
    ]

    use_console = env == "dev" and sys.stdout.isatty()
    renderer: Any = (
        structlog.dev.ConsoleRenderer() if use_console else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, etc.) through the same chain
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level.upper())

    # Silence noisy loggers — /health probes and httpx debug noise.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = [
    "PHONE_RE",
    "PII_KEYS",
    "configure_logging",
    "redact_pii",
]
