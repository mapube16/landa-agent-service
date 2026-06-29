"""Prompt firewall — sanitizes raw inbound text before LLM dispatch.

Implements OWASP LLM01 (prompt injection) mitigations:
- Unicode NFKC normalization + control-character strip
- Length cap 4000 chars (WhatsApp max is 4096; our cap leaves head-room)
- Regex pattern matching against ``INJECTION_PATTERNS`` catalog (top 10
  OWASP LLM01 patterns including ignore-previous, role override,
  instruction-prefix, delimiters)

``SanitizeResult.blocked=True`` means the message should NOT be forwarded to
the LLM. The webhook handler must send T-05 or an appropriate template when
blocked and NOT pass the raw text downstream.

``INJECTION_PATTERNS`` is populated in Plan 03-04 with the full catalog.
The stub here is an empty list so imports work without 03-04 being complete.

Implemented in: Plan 03-04.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger("security.prompt_firewall")

# Injection pattern catalog — populated in Plan 03-04.
# Each Pattern is pre-compiled regex for O(1) match per inbound message.
INJECTION_PATTERNS: list[re.Pattern[str]] = []  # ponytail: stub list, Plan 03-04 fills

__all__ = ["INJECTION_PATTERNS", "SanitizeResult", "sanitize"]


@dataclass
class SanitizeResult:
    """Result of running the prompt firewall over a raw inbound string."""

    blocked: bool
    reason: str = field(default="")
    cleaned: str = field(default="")


def sanitize(text: str) -> SanitizeResult:
    """Run the prompt firewall over ``text``.

    Returns ``SanitizeResult(blocked=False, cleaned=<normalized text>)`` on
    pass, or ``SanitizeResult(blocked=True, reason=<why>)`` on rejection.

    Implemented in Plan 03-04.
    """
    raise NotImplementedError("Implemented in Plan 03-04")
