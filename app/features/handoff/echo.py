"""Transitional echo handler (D-02) — implemented in Plan 02-02.

Lives in ``features/handoff/`` because it is pre-Phase 3: the moment
LangGraph entries replace the echo round-trip, this module is deleted.

Pure functions over ``settings`` + the inbound phone string. No I/O.

**E.164 normalization is obligatory** (RESEARCH Pitfall 8): Meta delivers
``from`` without the leading ``+``, while ``WA_ECHO_ALLOWLIST`` env-var
entries carry it. ``_normalize_e164`` is the equaliser.
"""

from __future__ import annotations

from app.config.settings import settings


def _normalize_e164(raw: str) -> str:
    """Return ``raw`` always prefixed with ``'+'``. Implemented in Plan 02-02."""
    raise NotImplementedError("Implemented in Plan 02-02")


def is_echo_allowed(phone: str) -> bool:
    """Return True iff ``phone`` (after E.164 normalization) is in WA_ECHO_ALLOWLIST.

    Implemented in Plan 02-02.
    """
    _ = settings.whatsapp.echo_allowlist
    raise NotImplementedError("Implemented in Plan 02-02")


def format_echo(text: str) -> str:
    """Return ``'echo: <text>'``. Implemented in Plan 02-02."""
    raise NotImplementedError("Implemented in Plan 02-02")


def format_media_echo(media_type: str) -> str:
    """Return ``'echo: [<media_type>] received'``. Implemented in Plan 02-02."""
    raise NotImplementedError("Implemented in Plan 02-02")


__all__ = ["format_echo", "format_media_echo", "is_echo_allowed"]
