"""Sentry SDK initialization with PII scrubber and FastAPI auto-detection.

Per CONTEXT.md D-07 + RESEARCH.md Pattern 4:
- ``send_default_pii=False`` is NON-negotiable (security domain).
- FastAPI/Starlette integrations are auto-detected — DO NOT manually wrap with
  ``SentryAsgiMiddleware`` (causes double-wrap that breaks ``request.body()``).
- ``before_send=scrub_sentry_event`` is the network-egress firewall: scrubs
  request data, breadcrumbs, exception local vars before exfiltration.
- ``asgi-correlation-id`` middleware (wired in main.py) injects ``X-Request-ID``
  which Sentry picks up as ``transaction_id`` automatically — no manual wiring.

Call ``init_sentry()`` BEFORE importing FastAPI routers in main.py
(integration auto-detection inspects ``sys.modules`` at init time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import sentry_sdk
from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config.logging import PHONE_RE, PII_KEYS
from app.config.settings import settings

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint


def _scrub_value(v: Any) -> Any:
    """Recursively scrub strings, dicts, and lists.

    Strings get phone-pattern redaction; dict keys matching ``PII_KEYS`` are
    redacted wholesale; everything else passes through.
    """
    if isinstance(v, str):
        return PHONE_RE.sub("[REDACTED_PHONE]", v)
    if isinstance(v, dict):
        return {
            k: ("[REDACTED]" if k.lower() in PII_KEYS else _scrub_value(val))
            for k, val in v.items()
        }
    if isinstance(v, list):
        return [_scrub_value(x) for x in v]
    return v


def scrub_sentry_event(event: Event, hint: Hint) -> Event | None:
    """Sentry ``before_send`` hook — PII firewall on outbound events.

    Scrubs:
      - ``request`` (headers, query, body)
      - ``contexts``
      - ``breadcrumbs``
      - ``exception`` (including nested frame locals)

    Returning ``None`` would drop the event entirely; we keep the event and
    redact in-place so debuggability survives.
    """
    del hint  # unused; required by Sentry's hook signature
    # ``Event`` is a TypedDict; cast to a plain mapping for in-place scrub
    # so the function works under mypy --strict whether or not the
    # ``sentry_sdk.types`` stubs are available in the type-check environment.
    ev = cast(dict[str, Any], event)
    for path in ("request", "contexts", "breadcrumbs", "exception"):
        if path in ev:
            ev[path] = _scrub_value(ev[path])

    # Aggressive pass on exception frame local vars (Pitfall 7 — tracebacks
    # leak phone numbers via repr of local vars even with send_default_pii=False).
    for ex in ev.get("exception", {}).get("values", []):
        for frame in ex.get("stacktrace", {}).get("frames", []):
            if "vars" in frame:
                frame["vars"] = {
                    k: ("[REDACTED]" if k.lower() in PII_KEYS else _scrub_value(v))
                    for k, v in frame["vars"].items()
                }
    return event


def init_sentry() -> None:
    """Initialise sentry-sdk if a DSN is configured.

    Idempotent: if ``settings.sentry.dsn`` is ``None`` (tests, CI without DSN),
    this is a no-op. In all other cases, applies the locked configuration:

    - ``send_default_pii=False``: never auto-attach request bodies/headers
    - ``before_send=scrub_sentry_event``: belt-and-braces redaction
    - FastAPI + Starlette + asyncpg + Redis integrations explicitly listed
      to make the integration surface auditable (auto-detection still works
      without these, but explicit > implicit for security-sensitive code)
    """
    if settings.sentry.dsn is None:
        return  # Sentry disabled

    sentry_sdk.init(
        dsn=settings.sentry.dsn.get_secret_value(),
        environment=settings.app.env,
        release=settings.app.version,
        traces_sample_rate=settings.sentry.traces_sample_rate,
        profiles_sample_rate=settings.sentry.profiles_sample_rate,
        send_default_pii=False,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncPGIntegration(),
            RedisIntegration(),
        ],
        before_send=scrub_sentry_event,
    )


__all__ = [
    "init_sentry",
    "scrub_sentry_event",
]
