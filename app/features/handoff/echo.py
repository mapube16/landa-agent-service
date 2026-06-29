"""Transitional echo handler (D-02) — implemented in Plan 02-02.

Lives in ``features/handoff/`` because it is pre-Phase 3: the moment
LangGraph entries replace the echo round-trip, this module is deleted.

Pure functions over ``settings`` + the inbound phone string. No I/O.

**E.164 normalization is obligatory** (RESEARCH Pitfall 8): Meta delivers
``from`` without the leading ``+``, while ``WA_ECHO_ALLOWLIST`` env-var
entries carry it. :func:`_normalize_e164` is the equaliser.
"""

from __future__ import annotations

from app.config.settings import settings


def _normalize_e164(raw: str) -> str:
    """Return ``raw`` always prefixed with ``'+'``. Idempotent.

    Meta sends ``from`` as ``"16505551234"`` (no leading ``+``); operators
    store allowlist entries as ``"+16505551234"`` (with ``+``). This helper
    normalises both sides to ``+`` form so set membership works (RESEARCH
    Pitfall 8).
    """
    raw = raw.strip()
    return raw if raw.startswith("+") else "+" + raw


def is_echo_allowed(phone: str) -> bool:
    """Return True iff ``phone`` (E.164-normalised) is in ``WA_ECHO_ALLOWLIST``."""
    normalized = _normalize_e164(phone)
    allowed = {_normalize_e164(p) for p in settings.whatsapp.echo_allowlist}
    return normalized in allowed


def format_echo(text: str) -> str:
    """Return ``'echo: <text>'`` — the literal echo response for text messages (D-02)."""
    return f"echo: {text}"


def format_media_echo(media_type: str) -> str:
    """Return ``'echo: [<media_type>] received'`` — echo response for non-text messages."""
    return f"echo: [{media_type}] received"


__all__ = ["format_echo", "format_media_echo", "is_echo_allowed"]
